"""Unit tests for the ask feature: BM25 retriever + answer synthesis.

No live LLM/network: the DSPY call is monkeypatched to a stub. ``ask`` is an
api_server-only feature, so these import from ``api_server.ask`` directly.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

from api_server.ask.core import answer_question
from api_server.ask.retriever import DocsRetriever
from models.ask import AskInput, AskResult
from tests.test_template import TestTemplate

_BASE_URL = "https://docs.example.com"


def _write_corpus(root: Path) -> None:
    (root / "guide").mkdir(parents=True, exist_ok=True)
    (root / "guide" / "setup.mdx").write_text(
        "---\ntitle: Setup\n---\n"
        "import { Callout } from 'x';\n\n"
        "# Installation\n"
        "Install the widget by running the installer command.\n\n"
        "## Configuration\n"
        "Configure the widget using the dashboard settings panel.\n",
        encoding="utf-8",
    )
    (root / "index.mdx").write_text(
        "# Overview\nThe quokka is a friendly marsupial mascot.\n",
        encoding="utf-8",
    )


class _StubResult:
    def __init__(self, answer: str):
        self.answer = answer


class TestDocsRetriever(TestTemplate):
    def test_chunking_and_ranking(self, tmp_path):
        _write_corpus(tmp_path)
        retriever = DocsRetriever(corpus_path=str(tmp_path), base_url=_BASE_URL)
        # Query targets the installation chunk specifically.
        results = retriever.search("install installer command", top_k=3)
        assert results, "expected at least one chunk"
        top = results[0]
        assert top.heading == "Installation"
        assert "installer" in top.text.lower()
        assert top.score > 0
        # URL mapping: drop .mdx, keep relative slug.
        assert top.url == f"{_BASE_URL}/guide/setup"

    def test_index_url_drops_trailing_index(self, tmp_path):
        _write_corpus(tmp_path)
        retriever = DocsRetriever(corpus_path=str(tmp_path), base_url=_BASE_URL)
        results = retriever.search("quokka marsupial mascot", top_k=1)
        assert results
        assert results[0].url == _BASE_URL

    def test_missing_corpus_is_graceful(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        retriever = DocsRetriever(corpus_path=str(missing), base_url=_BASE_URL)
        assert retriever.search("anything", top_k=5) == []


class TestAnswerQuestion(TestTemplate):
    def test_answer_shape_and_mapping(self, tmp_path):
        _write_corpus(tmp_path)
        retriever = DocsRetriever(corpus_path=str(tmp_path), base_url=_BASE_URL)

        async def _stub_run(self, **_kwargs):
            return _StubResult("Run the installer command.")

        with (
            patch("api_server.ask.core.get_retriever", return_value=retriever),
            patch("api_server.ask.core.DSPYInference.run", _stub_run),
        ):
            result: AskResult = asyncio.run(
                answer_question(AskInput(query="how do I install it?"))
            )

        assert isinstance(result, AskResult)
        assert result.answer == "Run the installer command."
        # query_id is generated when not supplied.
        assert result.query_id and len(result.query_id) > 0
        assert result.results
        item = result.results[0]
        assert item.schema_object["@type"] == "TechArticle"
        assert item.schema_object["@context"] == "https://schema.org"
        assert item.url.startswith(_BASE_URL)

    def test_query_id_is_preserved(self, tmp_path):
        _write_corpus(tmp_path)
        retriever = DocsRetriever(corpus_path=str(tmp_path), base_url=_BASE_URL)

        async def _stub_run(self, **_kwargs):
            return _StubResult("answer")

        with (
            patch("api_server.ask.core.get_retriever", return_value=retriever),
            patch("api_server.ask.core.DSPYInference.run", _stub_run),
        ):
            result = asyncio.run(
                answer_question(AskInput(query="q", query_id="fixed-id"))
            )
        assert result.query_id == "fixed-id"

    def test_empty_corpus_graceful_degrade(self, tmp_path):
        """No corpus -> empty results and the canned no-context answer."""
        retriever = DocsRetriever(
            corpus_path=str(tmp_path / "missing"), base_url=_BASE_URL
        )
        with patch("api_server.ask.core.get_retriever", return_value=retriever):
            result = asyncio.run(answer_question(AskInput(query="anything")))
        assert result.results == []
        assert "does not contain information" in result.answer
