"""Wire-level E2E tests for the streamable-HTTP /mcp endpoint.

Runs a full MCP session through the FastAPI mount - initialize,
notifications/initialized, tools/list, tools/call, resources/list,
resources/read - and asserts what actually crosses the wire: serialized
outputSchema, ``_meta.ui`` shapes, CallToolResult assembly, and
structuredContent. This is the fast in-CI tier of the two-tier strategy
from issue #42; MCPJam conformance (``make mcp_conformance``) is the
spec-conformance tier.

Complements ``tests/test_mcp_remote.py``, which covers the auth boundary
and stops at ``initialize``.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api_server.auth.api_key_auth import create_api_key
from api_server.server import app
from common import global_config, token_encryption
from common.token_encryption import FernetEncryption
from mcp_server._tool_factory import make_tool
from mcp_server.enhancers import _enhancers, enhance
from mcp_server.server import mcp
from models.curation import CurationBucket, ThreadJudgment
from services import _registry, get_registry, service
from services.curation_ledger import upsert_judgments
from tests._harness import patch_db, read_sse_first_message
from tests.test_template import TestTemplate

_PROTOCOL_VERSION = "2025-03-26"


class _McpSession:
    """Minimal JSON-RPC client speaking streamable HTTP against a TestClient."""

    def __init__(self, client: TestClient, api_key: str):
        self._client = client
        self._api_key = api_key
        self._session_id: str | None = None
        self._next_id = 0
        self._protocol_version = _PROTOCOL_VERSION

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": "127.0.0.1:8080",
            "X-API-KEY": self._api_key,
            # Post-initialize requests must carry the *negotiated* version,
            # not the one the client originally requested.
            "MCP-Protocol-Version": self._protocol_version,
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def request(self, method: str, params: dict | None = None) -> dict:
        """POST a JSON-RPC request; return the `result` member of the response."""
        self._next_id += 1
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        if self._session_id is None:
            self._session_id = resp.headers.get("mcp-session-id")
        msg = read_sse_first_message(resp)
        assert "error" not in msg, f"{method} returned error: {msg.get('error')}"
        return msg["result"]

    def request_error(self, method: str, params: dict | None = None) -> dict:
        """POST a JSON-RPC request expected to fail; return the `error` member."""
        self._next_id += 1
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        msg = read_sse_first_message(resp)
        assert "error" in msg, f"{method} unexpectedly succeeded: {msg}"
        return msg["error"]

    def notify(self, method: str) -> None:
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": method},
        )
        assert resp.status_code == 202, f"{method}: {resp.status_code} {resp.text}"

    def handshake(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "0"},
            },
        )
        self._protocol_version = result["protocolVersion"]
        self.notify("notifications/initialized")
        return result


@contextmanager
def _wire_session(user_id: str):
    """Yield a handshaken MCP session backed by a fresh DB and API key."""
    with patch("api_server.middleware.mcp_auth.global_config") as mock_config:
        mock_config.WORKOS_CLIENT_ID = None
        mock_config.WORKOS_AUTHKIT_DOMAIN = None
        with patch_db() as session_factory:
            with session_factory() as s:
                raw_key, _ = create_api_key(s, user_id=user_id, scopes=["*"])

            with TestClient(app) as client:
                session = _McpSession(client, raw_key)
                session.handshake()
                yield session


class TestMCPWireE2E(TestTemplate):
    def test_tools_list_serializes_schemas_and_app_meta(self):
        with _wire_session("u-e2e-list") as session:
            tools = {t["name"]: t for t in session.request("tools/list")["tools"]}

            # Enhanced tools publish outputSchema even though they return
            # CallToolResult (the _patch_output_schema path), and declare
            # their ui:// resource in tools/list per the MCP Apps spec.
            compose = tools["gmail_compose"]
            assert compose["outputSchema"]["type"] == "object"
            assert compose["_meta"]["ui"]["resourceUri"].startswith("ui://daysurface/")
            assert (
                compose["_meta"]["ui/resourceUri"]
                == (compose["_meta"]["ui"]["resourceUri"])
            )

            # Headless tools carry no UI metadata on the wire.
            assert "_meta" not in tools["gmail_send"] or (
                tools["gmail_send"].get("_meta") in (None, {})
            )

    def test_tools_call_returns_structured_content(self):
        with _wire_session("u-e2e-call") as session:
            result = session.request(
                "tools/call", {"name": "gmail_get_focused_email", "arguments": {}}
            )
            # No focused thread for a fresh user; auth guard substitutes the
            # verified principal for the (empty) wire user_id.
            assert result["structuredContent"]["focused"] is False
            assert result["isError"] is False
            text_block = result["content"][0]
            assert text_block["type"] == "text"
            assert json.loads(text_block["text"])["focused"] is False

    def test_resources_read_serves_app_html(self):
        with _wire_session("u-e2e-res") as session:
            resources = session.request("resources/list")["resources"]
            ui_resources = [
                r for r in resources if str(r["uri"]).startswith("ui://daysurface/")
            ]
            assert ui_resources, "expected ui:// resources in resources/list"
            assert all(
                r["mimeType"] == "text/html;profile=mcp-app" for r in ui_resources
            )

            contents = session.request(
                "resources/read", {"uri": ui_resources[0]["uri"]}
            )["contents"]
            assert len(contents) == 1
            assert contents[0]["mimeType"] == "text/html;profile=mcp-app"
            assert contents[0]["text"].lstrip().lower().startswith("<!doctype html>")

    def test_inbox_get_curation_over_wire(self):
        """inbox_get_curation crosses the wire as a real enhanced tool: banked
        verdicts + coverage in structuredContent, and the gmail_inbox app in
        both tools/list and the CallToolResult _meta.ui."""
        user = "u-e2e-curation"
        enc = FernetEncryption(Fernet.generate_key().decode())
        stubs = [{"id": "t1", "historyId": "100"}]
        with (
            patch.object(token_encryption, "require_encryption", return_value=enc),
            _wire_session(user) as session,
        ):
            # Bank one verdict into the same in-memory DB the wire uses.
            upsert_judgments(
                user,
                [
                    ThreadJudgment(
                        thread_id="t1",
                        bucket=CurationBucket.needs_reply,
                        importance=0.9,
                        summary="deck due Friday",
                    )
                ],
                history_ids={"t1": "100"},
            )
            with (
                patch("services.inbox_curation_svc._get_gmail_client"),
                patch(
                    "services.inbox_curation_svc._list_thread_stubs",
                    return_value=stubs,
                ),
            ):
                tools = {t["name"]: t for t in session.request("tools/list")["tools"]}
                # Enhanced read tool: publishes outputSchema + its ui:// app.
                gc = tools["inbox_get_curation"]
                assert gc["outputSchema"]["type"] == "object"
                assert gc["_meta"]["ui"]["resourceUri"] == "ui://daysurface/gmail_inbox"
                # The mutating write-back stays headless (no UI on the wire).
                save_meta = tools["inbox_save_curation"].get("_meta")
                assert save_meta in (None, {})

                result = session.request(
                    "tools/call", {"name": "inbox_get_curation", "arguments": {}}
                )
                sc = result["structuredContent"]
                assert sc["coverage"] == {"curated": 1, "stale": 0, "uncurated": 0}
                assert sc["records"][0]["thread_id"] == "t1"
                assert sc["records"][0]["summary"] == "deck due Friday"
                assert sc["records"][0]["ledger_status"] == "curated"
                assert result["isError"] is False
                # Enhancer attached the dashboard app on the result.
                assert (
                    result["_meta"]["ui"]["resourceUri"]
                    == "ui://daysurface/gmail_inbox"
                )

    def test_not_connected_gmail_tool_returns_url_elicitation_error(self):
        """A Gmail tool called by a user with no linked account surfaces the
        SEP-1036 URL-elicitation-required error (JSON-RPC -32042) carrying the
        Google consent URL - on both the headless (gmail_list_inbox) and
        enhanced (inbox_get_curation) registration paths. The stateless mount
        never sees client capabilities, so conversion is the default."""
        with (
            patch.object(global_config, "GOOGLE_CLIENT_ID", "e2e-client"),
            patch.object(
                global_config,
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/v1/auth/google/callback",
            ),
            _wire_session("u-e2e-nolink") as session,
        ):
            for tool_name in ("gmail_list_inbox", "inbox_get_curation"):
                error = session.request_error(
                    "tools/call", {"name": tool_name, "arguments": {}}
                )
                assert error["code"] == -32042, f"{tool_name}: {error}"
                elic = error["data"]["elicitations"][0]
                assert elic["mode"] == "url"
                assert elic["elicitationId"].startswith("connect-")
                assert elic["url"].startswith("https://accounts.google.com/")
                # Hosts that don't understand -32042 fall back to the message
                # text alone, so it must stay self-recovering on its own.
                assert "gmail_connect" in error["message"]
                assert elic["url"] in error["message"]

    def test_enhanced_tool_call_assembles_full_result_on_wire(self):
        """Register a throwaway enhanced tool and call it through the wire,
        asserting the CallToolResult parts (text + extra content, _meta.ui,
        structuredContent) as serialized - not via in-process imports."""

        class _In(BaseModel):
            x: int = 0

        class _Out(BaseModel):
            value: int

        svc_name = "__e2e_enhanced_test"

        @service(name=svc_name, description="e2e", input_model=_In, output_model=_Out)
        def _svc(input: _In) -> _Out:
            return _Out(value=input.x * 3)

        @enhance(svc_name, fallback="headless")
        async def _enhancer(tool):
            result = tool.call()
            tool.send_text("extra block", audience=["user"])
            tool.send_app("ui://daysurface/__e2e_test_app")
            return result

        entry = next(e for e in get_registry() if e.name == svc_name)
        make_tool(mcp, entry)
        try:
            with _wire_session("u-e2e-enh") as session:
                result = session.request(
                    "tools/call", {"name": svc_name, "arguments": {"x": 7}}
                )
                assert result["structuredContent"] == {"value": 21}
                blocks = result["content"]
                assert json.loads(blocks[0]["text"]) == {"value": 21}
                assert blocks[1]["text"] == "extra block"
                assert blocks[1]["annotations"]["audience"] == ["user"]
                meta = result["_meta"]
                assert meta["ui"]["resourceUri"] == "ui://daysurface/__e2e_test_app"
                assert meta["ui/resourceUri"] == "ui://daysurface/__e2e_test_app"
        finally:
            _registry[:] = [e for e in _registry if e.name != svc_name]
            _enhancers.pop(svc_name, None)
            mcp._tool_manager._tools.pop(svc_name, None)


class TestPdfToolsWireE2E(TestTemplate):
    """US-004/FR-10: page images cross the wire as ImageContent, and no PDF
    bytes ever appear in an LLM-visible tool result."""

    def test_pdf_open_renders_image_content_and_leaks_no_pdf_bytes(self):
        from models.pdf_forms import PdfOpenResult  # noqa: PLC0415 - test-local
        from services.pdf_ports import ResolvedPdfSource  # noqa: PLC0415 - test-local
        from tests.pdf_fixtures import make_flat_pdf  # noqa: PLC0415 - test-local

        pdf_bytes = make_flat_pdf()

        def _stub_resolver(user_id, source):
            return ResolvedPdfSource(filename="nda.pdf", data=pdf_bytes)

        with (
            patch("services.pdf_forms_svc.resolve_source", _stub_resolver),
            _wire_session("u-e2e-pdf") as session,
        ):
            result = session.request(
                "tools/call",
                {
                    "name": "pdf_open",
                    "arguments": {
                        "source": {
                            "type": "gmail_attachment",
                            "message_id": "m1",
                            "attachment_id": "a1",
                        },
                        "render_pages": [1],
                    },
                },
            )
            assert result["isError"] is False
            structured = result["structuredContent"]
            assert structured["doc_id"]
            assert structured["has_acroform"] is False
            assert structured["page_count"] == 1
            # Valid per outputSchema: the enhancer moved the PNGs out of the
            # structured result into ImageContent blocks.
            PdfOpenResult.model_validate(structured)
            assert structured["page_images"] == []
            images = [b for b in result["content"] if b["type"] == "image"]
            assert len(images) == 1
            assert images[0]["mimeType"] == "image/png"

            # FR-10: no PDF bytes in any LLM-visible form - neither raw nor
            # base64 ("%PDF" encodes to "JVBERi" at offset 0).
            wire_payload = json.dumps(result)
            assert "%PDF" not in wire_payload
            assert "JVBERi" not in wire_payload

    def test_pdf_tools_are_listed_with_output_schema(self):
        with _wire_session("u-e2e-pdf-list") as session:
            tools = {t["name"]: t for t in session.request("tools/list")["tools"]}
            for name in ("pdf_open", "pdf_edit", "pdf_export"):
                assert name in tools, f"{name} missing from tools/list"
                assert tools[name]["outputSchema"]["type"] == "object"
            # The signing gate declares the pdf_signer app in tools/list so
            # hosts can pre-fetch the iframe HTML.
            gate = tools["pdf_request_signature"]
            assert gate["_meta"]["ui"]["resourceUri"] == "ui://daysurface/pdf_signer"
            # App-only ceremony tools carry the visibility hint on the wire.
            sign_tool = tools["pdf_signer.sign"]
            assert sign_tool["_meta"]["ui"]["visibility"] == ["app"]

    def test_pdf_signer_app_resource_serves_built_html(self):
        with _wire_session("u-e2e-pdf-app") as session:
            contents = session.request(
                "resources/read", {"uri": "ui://daysurface/pdf_signer"}
            )["contents"]
            assert contents[0]["mimeType"] == "text/html;profile=mcp-app"
            html = contents[0]["text"]
            assert html.lstrip().lower().startswith("<!doctype html>")
            # The committed bundle is the built app, not the placeholder.
            assert "not built" not in html
