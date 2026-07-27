"""Tests for the RFC 9727 API catalog served at /.well-known/api-catalog.

The catalog points agents and crawlers at the OpenAPI description; like the
server card it is unauthenticated and must be readable cross-origin.
"""

from fastapi.testclient import TestClient

from api_server.routes import well_known
from api_server.server import app

CATALOG_PATH = "/.well-known/api-catalog"


class TestApiCatalog:
    def _client(self) -> TestClient:
        # No lifespan needed: this route never reaches the MCP sub-app.
        return TestClient(app)

    def test_catalog_links_to_openapi(self, monkeypatch):
        # No public URL configured: the linkset uses relative hrefs the client
        # resolves against the request origin.
        monkeypatch.setattr(well_known.global_config, "API_PUBLIC_URL", None)
        resp = self._client().get(CATALOG_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/linkset+json")

        link = resp.json()["linkset"][0]
        assert link["anchor"] == "/"
        desc = link["service-desc"][0]
        assert desc["href"] == "/openapi.json"
        assert desc["type"] == "application/vnd.oai.openapi+json"

    def test_catalog_uses_absolute_urls_when_configured(self, monkeypatch):
        # With a public URL set, hrefs are absolute and match the OpenAPI
        # `servers` block so crawlers fetch the spec from the real deployment.
        monkeypatch.setattr(
            well_known.global_config, "API_PUBLIC_URL", "https://api.example.com/"
        )
        link = self._client().get(CATALOG_PATH).json()["linkset"][0]
        assert link["anchor"] == "https://api.example.com/"
        assert link["service-desc"][0]["href"] == "https://api.example.com/openapi.json"

    def test_catalog_tracks_custom_openapi_url(self, monkeypatch):
        # The href is derived from the app's openapi_url, not hardcoded, so a
        # customized spec path stays in sync.
        monkeypatch.setattr(well_known.global_config, "API_PUBLIC_URL", None)
        monkeypatch.setattr(app, "openapi_url", "/custom/openapi.json")
        desc = self._client().get(CATALOG_PATH).json()["linkset"][0]["service-desc"][0]
        assert desc["href"] == "/custom/openapi.json"

    def test_catalog_is_cors_readable(self):
        # Agents and registry crawlers fetch the catalog cross-origin.
        resp = self._client().get(CATALOG_PATH)
        assert resp.headers.get("access-control-allow-origin") == "*"
