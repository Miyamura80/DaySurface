"""Tests for the A2A Agent Card served at /.well-known/agent-card.json.

The Agent Card is the Agent2Agent (spec v0.3.0) pre-connect discovery document.
Like the MCP Server Card it must be available regardless of auth config and be
readable cross-origin, and it must validate against the A2A AgentCard schema (a
self-contained copy lives in ``tests/data/`` so the test needs no network).
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api_server.routes import well_known
from api_server.server import app
from services import discover_services, get_registry

CARD_PATH = "/.well-known/agent-card.json"
SCHEMA_PATH = Path(__file__).parent / "data" / "a2a_agent_card.schema.json"


class TestAgentCard:
    def _client(self) -> TestClient:
        # No lifespan needed: this route never reaches the MCP sub-app.
        return TestClient(app)

    def test_card_validates_against_a2a_schema(self):
        resp = self._client().get(CARD_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        schema = json.loads(SCHEMA_PATH.read_text())
        # Raises ValidationError (failing the test) if the card is non-conformant.
        Draft202012Validator(schema).validate(resp.json())

    def test_card_carries_branding_identity(self):
        body = self._client().get(CARD_PATH).json()
        assert body["protocolVersion"] == "0.3.0"
        assert body["name"]
        assert body["description"]
        assert body["url"].startswith("https://")
        assert body["provider"]["organization"]
        # Discovery-only card: no transport binding is claimed, because the
        # template implements no A2A wire transport (JSON-RPC / HTTP+JSON).
        assert "preferredTransport" not in body

    def test_skills_mirror_the_service_registry(self):
        discover_services()
        registry_names = {entry.name for entry in get_registry()}
        assert registry_names, "expected at least one registered service"

        body = self._client().get(CARD_PATH).json()
        skill_ids = {skill["id"] for skill in body["skills"]}
        assert skill_ids == registry_names
        # Every skill carries the required A2A fields populated, not just present.
        for skill in body["skills"]:
            assert skill["name"] and skill["description"] and skill["tags"]

    def test_card_uses_website_url_without_public_host(self, monkeypatch):
        # No public host configured (e.g. local/dev): the required `url` must fall
        # back to the branding website, never the localhost dev default.
        monkeypatch.setattr(well_known.global_config, "API_PUBLIC_URL", None)
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", None)
        body = self._client().get(CARD_PATH).json()
        assert "localhost" not in body["url"]
        assert body["url"] == well_known.global_config.branding.website_url.rstrip("/")

    def test_card_points_url_at_mcp_endpoint(self, monkeypatch):
        # The MCP endpoint is the agent's real machine surface, so it takes
        # precedence over the API host when both are configured.
        monkeypatch.setattr(
            well_known.global_config, "MCP_PUBLIC_URL", "https://mcp.example.com/mcp"
        )
        monkeypatch.setattr(
            well_known.global_config, "API_PUBLIC_URL", "https://api.example.com"
        )
        body = self._client().get(CARD_PATH).json()
        assert body["url"] == "https://mcp.example.com/mcp"

    def test_card_falls_back_to_api_host(self, monkeypatch):
        # No MCP endpoint but an API host configured: advertise the API host.
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", None)
        monkeypatch.setattr(
            well_known.global_config, "API_PUBLIC_URL", "https://api.example.com"
        )
        body = self._client().get(CARD_PATH).json()
        assert body["url"] == "https://api.example.com"

    def test_card_is_cors_readable(self):
        # A2A crawlers fetch the card cross-origin; it must allow that.
        resp = self._client().get(CARD_PATH)
        assert resp.headers.get("access-control-allow-origin") == "*"
