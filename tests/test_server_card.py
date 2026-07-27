"""Tests for the SEP-2127 MCP Server Card served at /.well-known/mcp/.

The card is pre-connect branding, so unlike the OAuth metadata it must be
available regardless of auth config and readable cross-origin.
"""

from fastapi.testclient import TestClient

from api_server.routes import well_known
from api_server.server import app

CARD_PATH = "/.well-known/mcp/server-card.json"


class TestServerCard:
    def _client(self) -> TestClient:
        # No lifespan needed: this route never reaches the MCP sub-app.
        return TestClient(app)

    def test_card_served_with_branding(self):
        resp = self._client().get(CARD_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        body = resp.json()
        # The fields the registry audit checks for: name, description, icon.
        assert body["name"] == "io.github.Miyamura80/MCP-Template"
        assert body["title"]
        assert body["description"]
        assert body["version"]
        assert body["icons"] and body["icons"][0]["src"].startswith("https://")
        # No $schema: the draft SEP-2127 server-card schema URL is unpublished (404).
        assert "$schema" not in body

    def test_card_advertises_tool_surface(self):
        # Pre-connect discovery: agents preview the tool surface before connecting.
        body = self._client().get(CARD_PATH).json()
        tools = body["tools"]
        assert tools, "server card must advertise tools[] for pre-connect discovery"
        assert all(t["name"] and t["description"] for t in tools)
        names = {t["name"] for t in tools}
        # A representative public Gmail tool is exposed...
        assert "gmail_curate_inbox" in names
        # ...while CLI-only defaults are not part of the MCP tool surface.
        assert "doctor" not in names
        assert "config_set" not in names

    def test_card_advertises_server_url_when_public(self, monkeypatch):
        url = "https://mcp.example.com/mcp"
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", url)
        body = self._client().get(CARD_PATH).json()
        # `serverUrl` (flat) and `remotes` (SEP shape) name the same endpoint.
        assert body["serverUrl"] == url
        assert body["remotes"][0] == {"type": "streamable-http", "url": url}

    def test_card_omits_server_url_without_public_url(self, monkeypatch):
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", None)
        body = self._client().get(CARD_PATH).json()
        assert "serverUrl" not in body

    def test_card_omits_remote_without_public_url(self, monkeypatch):
        # MCP_PUBLIC_URL unset (e.g. deployed no-OAuth server): the card must not
        # advertise the localhost fallback as a discoverable endpoint.
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", None)
        body = self._client().get(CARD_PATH).json()
        assert "localhost" not in str(body)
        assert not body.get("remotes")

    def test_card_advertises_public_remote_when_configured(self, monkeypatch):
        url = "https://mcp.example.com/mcp"
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", url)
        body = self._client().get(CARD_PATH).json()
        remotes = body["remotes"]
        assert remotes and remotes[0] == {"type": "streamable-http", "url": url}

    def test_card_is_cors_readable(self):
        # Registry crawlers fetch the card cross-origin; it must allow that.
        resp = self._client().get(CARD_PATH)
        assert resp.headers.get("access-control-allow-origin") == "*"
