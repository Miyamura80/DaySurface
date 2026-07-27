"""Wire-level E2E tests for the NLWeb /ask endpoint.

Uses a FastAPI TestClient against the real app. The LLM core is patched so no
network call happens, and the retriever points at a tiny temp corpus.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_server.ask.retriever import DocsRetriever
from api_server.server import app
from common import global_config
from models.ask import AskResult
from tests.test_template import TestTemplate

_BASE_URL = "https://docs.example.com"


def _write_corpus(root: Path) -> None:
    (root / "page.mdx").write_text(
        "# Setup\nInstall the server by running the installer command.\n",
        encoding="utf-8",
    )


class _StubResult:
    answer = "Run the installer command to set up the server."


async def _stub_run(self, **_kwargs):
    return _StubResult()


@contextmanager
def _enabled_ask(tmp_path: Path):
    """Enable /ask, patch the retriever corpus and the LLM call."""
    _write_corpus(tmp_path)
    retriever = DocsRetriever(corpus_path=str(tmp_path), base_url=_BASE_URL)
    original = global_config.ask.enabled
    global_config.ask.enabled = True
    try:
        with (
            patch("api_server.ask.core.get_retriever", return_value=retriever),
            patch("api_server.ask.core.DSPYInference.run", _stub_run),
        ):
            yield
    finally:
        global_config.ask.enabled = original


class TestAskE2E(TestTemplate):
    def test_non_streaming_returns_documented_json(self, tmp_path):
        with _enabled_ask(tmp_path), TestClient(app) as client:
            resp = client.post(
                "/ask", json={"query": "how do I install?", "streaming": False}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "query_id" in body
        assert isinstance(body["results"], list)
        # SearchSummary answer is index 0.
        summary = body["results"][0]
        assert summary["schema_object"]["@type"] == "SearchSummary"
        assert "installer" in summary["description"].lower()
        # At least one retrieved doc chunk follows.
        assert any(
            r["schema_object"].get("@type") == "TechArticle" for r in body["results"]
        )

    def test_streaming_emits_nlweb_events(self, tmp_path):
        with (
            _enabled_ask(tmp_path),
            TestClient(app) as client,
            client.stream(
                "post", "/ask", json={"query": "install", "streaming": True}
            ) as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = [
                json.loads(line.removeprefix("data:").strip())
                for line in resp.iter_lines()
                if line.startswith("data:")
            ]
        types = [e["message_type"] for e in events]
        assert types[0] == "start"
        assert types[-1] == "complete"
        assert "result" in types
        assert events[0]["_meta"]["response_type"] == "nlws"
        assert "query_id" in events[0]["_meta"]
        assert "session_context" in events[-1]["_meta"]

    def test_disabled_returns_404(self, tmp_path):
        # ask.enabled defaults to False; do not enable it here.
        with TestClient(app) as client:
            resp = client.post("/ask", json={"query": "x", "streaming": False})
        assert resp.status_code == 404

    def test_streaming_failure_still_completes(self, tmp_path):
        async def _boom(self, **_kwargs):
            raise ValueError("no API key configured")

        with (
            _enabled_ask(tmp_path),
            patch("api_server.ask.core.DSPYInference.run", _boom),
            TestClient(app) as client,
            client.stream(
                "post", "/ask", json={"query": "install", "streaming": True}
            ) as resp,
        ):
            assert resp.status_code == 200
            events = [
                json.loads(line.removeprefix("data:").strip())
                for line in resp.iter_lines()
                if line.startswith("data:")
            ]
        types = [e["message_type"] for e in events]
        # Stream still closes cleanly with a complete event carrying the error.
        assert types[0] == "start"
        assert types[-1] == "complete"
        assert events[-1]["_meta"]["error"] == "answer generation failed"

    def test_get_invalid_mode_returns_422(self):
        # Literal-typed query param is validated at the boundary -> clean 422,
        # not a route-time crash. Validation happens before the enabled guard.
        with TestClient(app) as client:
            resp = client.get("/ask", params={"query": "x", "mode": "bogus"})
        assert resp.status_code == 422

    def test_get_parses_prev_into_list(self, tmp_path):
        captured = {}

        async def _capture(inp):
            captured["prev"] = inp.prev
            return AskResult(query_id="qid", answer="a", results=[])

        with (
            _enabled_ask(tmp_path),
            patch("api_server.routes.ask.answer_question", _capture),
            TestClient(app) as client,
        ):
            resp = client.get(
                "/ask",
                params={"query": "x", "streaming": "false", "prev": "first, second"},
            )
        assert resp.status_code == 200
        assert captured["prev"] == ["first", "second"]
