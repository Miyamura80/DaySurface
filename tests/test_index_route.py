"""Tests for the root landing document and the unknown-path 404 fallback.

The host exists to serve MCP, so both are part of the agent journey: an agent
handed ``https://mcp.<domain>`` (no path) must be able to find the endpoint,
and one that guesses a wrong path must be told the right one instead of
Starlette's bare ``Not Found``.
"""

from fastapi.testclient import TestClient

from api_server.routes import index
from api_server.server import app


class TestRootIndex:
    def _client(self) -> TestClient:
        # No lifespan needed: neither handler touches the MCP session manager.
        return TestClient(app)

    def test_root_describes_the_mcp_endpoint(self):
        resp = self._client().get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        body = resp.json()
        assert body["protocol"] == "mcp"
        assert body["title"] and body["description"]
        assert body["mcp"]["url"].endswith("/mcp")
        assert body["mcp"]["transport"] == "streamable-http"
        # /mcp is never anonymous, so an agent must learn that up front.
        assert body["mcp"]["authentication"]["required"] is True
        assert "api_key" in body["mcp"]["authentication"]["schemes"]

    def test_root_lists_the_tool_surface_and_discovery_endpoints(self):
        body = self._client().get("/").json()
        assert body["mcp"]["tools"], "root document must advertise the tool names"
        endpoints = body["endpoints"]
        for name in ("mcp", "health", "openapi", "server_card", "agent_card"):
            assert endpoints[name].startswith("http")

    def test_root_is_cors_readable(self):
        # Registry crawlers and browser-based agents read it cross-origin.
        assert self._client().get("/").headers.get("access-control-allow-origin") == "*"

    def test_root_prefers_the_configured_public_host(self, monkeypatch):
        # Absolute URLs must match what OAuth binds tokens to, not the request
        # origin, which behind a proxy can be the internal hostname.
        monkeypatch.setattr(
            index.global_config, "MCP_PUBLIC_URL", "https://mcp.example.com/mcp"
        )
        body = self._client().get("/").json()
        assert body["mcp"]["url"] == "https://mcp.example.com/mcp"
        assert body["endpoints"]["health"] == "https://mcp.example.com/health"

    def test_root_serves_html_to_browsers(self):
        resp = self._client().get("/", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "/mcp" in resp.text
        # The copy-pasteable connect command is the point of the page.
        assert "claude mcp add --transport http" in resp.text
        # Self-contained: no external assets to block or leak the visit.
        assert "src=" not in resp.text

    def test_head_root_is_supported(self):
        # Uptime probes and link checkers send HEAD before GET.
        assert self._client().head("/").status_code == 200

    def test_root_stays_out_of_the_openapi_spec(self):
        assert "/" not in app.openapi()["paths"]


class TestNotFoundFallback:
    def _client(self) -> TestClient:
        return TestClient(app)

    def test_unknown_path_points_at_the_mcp_endpoint(self):
        resp = self._client().get("/definitely-not-a-route")
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")

        body = resp.json()
        # The API's standard error envelope, request ID stamped by the
        # error-handler middleware.
        assert body["error"]["code"] == "not_found"
        assert body["error"]["request_id"]
        assert body["error"]["details"]["path"] == "/definitely-not-a-route"
        assert "/mcp" in body["error"]["message"]
        assert body["endpoints"]["mcp"].endswith("/mcp")

    def test_legacy_transport_paths_suggest_the_mcp_endpoint(self):
        # Clients written against the old HTTP+SSE transport probe these first.
        client = self._client()
        for path in ("/sse", "/messages", "/rpc"):
            body = client.get(path).json()
            assert body["error"]["details"]["did_you_mean"].endswith("/mcp"), path

    def test_registry_probes_suggest_the_server_card(self):
        body = self._client().get("/.well-known/mcp.json").json()
        did_you_mean = body["error"]["details"]["did_you_mean"]
        assert did_you_mean.endswith("/.well-known/mcp/server-card.json")

    def test_near_miss_paths_suggest_the_closest_endpoint(self):
        body = self._client().get("/helth").json()
        assert body["error"]["details"]["did_you_mean"].endswith("/health")

    def test_fallback_does_not_shadow_the_mcp_mount(self):
        # /mcp is still FastMCP's; unauthenticated callers get the JSON-RPC 401
        # challenge, not the landing-page 404.
        resp = self._client().get("/mcp")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == -32001
        assert "www-authenticate" in resp.headers

    def test_real_routes_are_untouched(self):
        assert self._client().get("/health").status_code == 200
