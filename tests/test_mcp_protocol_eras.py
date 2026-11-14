"""Protocol-era E2E over the /mcp mount (python-sdk 2.x, spec 2026-07-28).

One ``streamable_http_app`` serves both eras, routed by the
``MCP-Protocol-Version`` header:

- **2026-07-28** (stateless core): self-contained POSTs with no
  ``initialize`` and no ``Mcp-Session-Id``; version, client info and
  capabilities ride each request's ``_meta``; ``server/discover`` replaces the
  handshake.
- **Handshake era** (2025-11-25 and earlier): ``initialize`` +
  ``notifications/initialized``, then the negotiated version on every request.
  ``tests/test_mcp_e2e.py`` covers this era's wire shapes in depth.
"""

from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from common import global_config
from tests.test_mcp_e2e import (
    _McpSession,
    _read_sse_first_message,
    _wire_client,
    _wire_session,
)
from tests.test_template import TestTemplate

_LEGACY_LATEST = "2025-11-25"
_MODERN = "2026-07-28"


# Request-name header per method (SEP-2243 routing headers, 2026-07-28).
_NAME_PARAM = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}


class _ModernMcpClient:
    """Speaks the 2026-07-28 stateless wire: one self-contained POST per call.

    No ``initialize``, no ``Mcp-Session-Id``. Every request repeats the
    protocol version in the ``MCP-Protocol-Version`` header and in
    ``params._meta`` (with client info + capabilities), and mirrors the method
    (and tool name / resource URI) in the ``Mcp-Method`` / ``Mcp-Name``
    routing headers the server cross-checks.
    """

    def __init__(self, client: TestClient, api_key: str, capabilities=None):
        self._client = client
        self._api_key = api_key
        self._capabilities = capabilities if capabilities is not None else {}
        self._next_id = 0

    def post(self, method: str, params: dict | None = None):
        self._next_id += 1
        params = dict(params or {})
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientInfo": {
                "name": "e2e-modern",
                "version": "0",
            },
            "io.modelcontextprotocol/clientCapabilities": self._capabilities,
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": "127.0.0.1:8080",
            "X-API-KEY": self._api_key,
            "MCP-Protocol-Version": _MODERN,
            "Mcp-Method": method,
        }
        if method in _NAME_PARAM:
            headers["Mcp-Name"] = str(params[_NAME_PARAM[method]])
        resp = self._client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params,
            },
        )
        # Stateless core: the server never mints a session.
        assert "mcp-session-id" not in resp.headers, resp.headers
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            return resp, _read_sse_first_message(resp)
        return resp, resp.json()

    def request(self, method: str, params: dict | None = None) -> dict:
        resp, msg = self.post(method, params)
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        assert "error" not in msg, f"{method} returned error: {msg.get('error')}"
        return msg["result"]

    def request_error(self, method: str, params: dict | None = None) -> dict:
        _resp, msg = self.post(method, params)
        assert "error" in msg, f"{method} unexpectedly succeeded: {msg}"
        return msg["error"]


@contextmanager
def _google_configured():
    with (
        patch.object(global_config, "GOOGLE_CLIENT_ID", "e2e-client"),
        patch.object(
            global_config,
            "GOOGLE_REDIRECT_URI",
            "http://localhost:8000/api/v1/auth/google/callback",
        ),
    ):
        yield


class TestMCPProtocolEras(TestTemplate):
    """The one /mcp mount serves 2026-07-28 stateless requests AND
    handshake-era sessions side by side (python-sdk 2.x era routing)."""

    def test_modern_server_discover_replaces_initialize(self):
        with _wire_client("u-era-discover") as (client, key):
            result = _ModernMcpClient(client, key).request("server/discover")
            assert _MODERN in result["supportedVersions"]
            caps = result["capabilities"]
            assert "tools" in caps
            assert "resources" in caps
            # MCP Apps is negotiated as an extension in 2026-07-28.
            assert "io.modelcontextprotocol/ui" in caps["extensions"]
            assert result["instructions"]
            # serverInfo moved into per-result _meta.
            info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
            assert info["name"] == "daysurface"

    def test_modern_stateless_list_and_call_without_handshake(self):
        with _wire_client("u-era-modern") as (client, key):
            modern = _ModernMcpClient(client, key)
            tools = {t["name"]: t for t in modern.request("tools/list")["tools"]}
            compose = tools["gmail_compose"]
            assert compose["outputSchema"]["type"] == "object"
            assert compose["_meta"]["ui"]["resourceUri"].startswith("ui://daysurface/")

            result = modern.request(
                "tools/call", {"name": "gmail_get_focused_email", "arguments": {}}
            )
            assert result["isError"] is False
            assert result["structuredContent"]["focused"] is False
            assert result["resultType"] == "complete"
            assert "io.modelcontextprotocol/serverInfo" in result["_meta"]

            contents = modern.request(
                "resources/read", {"uri": "ui://daysurface/pdf_signer"}
            )["contents"]
            assert contents[0]["mimeType"] == "text/html;profile=mcp-app"

    def test_modern_url_capable_client_gets_url_elicitation_error(self):
        """Capabilities are known per request on 2026-07-28: a client that
        declares ``elicitation.url`` gets the -32042 error carrying the consent
        URL."""
        with _google_configured(), _wire_client("u-era-url") as (client, key):
            modern = _ModernMcpClient(
                client, key, capabilities={"elicitation": {"url": {}}}
            )
            error = modern.request_error(
                "tools/call", {"name": "gmail_list_inbox", "arguments": {}}
            )
            assert error["code"] == -32042
            elic = error["data"]["elicitations"][0]
            assert elic["mode"] == "url"
            assert elic["url"].startswith("https://accounts.google.com/")
            # 2026-07-28 removed elicitationId (outcome = retry the call).
            assert "elicitationId" not in elic

    def test_modern_form_only_client_gets_self_recovering_text(self):
        """A client that declared capabilities without url-mode elicitation
        gets an isError result whose text is the first-party recovery script -
        not the SDK's sanitized ``Error executing tool <name>``."""
        with _google_configured(), _wire_client("u-era-form") as (client, key):
            modern = _ModernMcpClient(client, key, capabilities={})
            result = modern.request(
                "tools/call", {"name": "gmail_list_inbox", "arguments": {}}
            )
            assert result["isError"] is True
            text = result["content"][0]["text"]
            assert "gmail_connect" in text

    def test_legacy_2025_11_25_handshake_session(self):
        with _wire_session("u-era-legacy", protocol_version=_LEGACY_LATEST) as s:
            assert s._protocol_version == _LEGACY_LATEST
            result = s.request(
                "tools/call", {"name": "gmail_get_focused_email", "arguments": {}}
            )
            assert result["isError"] is False
            assert result["structuredContent"]["focused"] is False

    def test_legacy_unknown_version_negotiates_down_to_latest_handshake(self):
        with _wire_client("u-era-negotiate") as (client, key):
            session = _McpSession(client, key, protocol_version="2099-01-01")
            result = session.handshake()
            assert result["protocolVersion"] == _LEGACY_LATEST
            assert session.request("tools/list")["tools"]
