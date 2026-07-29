"""Tests for the root landing document and the unknown-path 404 fallback.

This deployment exists to serve MCP, so both are part of the agent journey: an
agent handed ``https://mcp.<domain>`` (no path) must be able to find the
endpoint, and one that guesses a wrong path must be told the right one instead
of Starlette's bare ``Not Found``.
"""

from fastapi.testclient import TestClient

from api_server.routes import index
from api_server.server import app
from tests.test_template import TestTemplate


class TestRootIndex(TestTemplate):
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

    def test_root_varies_on_accept(self):
        # The host sits behind a CDN; without Vary the first cached response is
        # served to everyone, handing agents HTML or browsers JSON.
        client = self._client()
        assert client.get("/").headers.get("vary") == "Accept"
        html_resp = client.get("/", headers={"accept": "text/html"})
        assert html_resp.headers.get("vary") == "Accept"

    def test_every_advertised_endpoint_resolves(self):
        # An endpoint map that points at a 404 is worse than no map: it sends an
        # agent down a dead path with full confidence. Walk what we advertise.
        client = self._client()
        for name, url in client.get("/").json()["endpoints"].items():
            path = url.replace("http://testserver", "")
            assert client.get(path).status_code != 404, f"{name} -> {path}"

    def test_oauth_endpoints_are_advertised_only_when_configured(self, monkeypatch):
        # The OAuth documents 404 when AuthKit is unconfigured, so advertising
        # them would contradict `mcp.authentication` in the same document.
        body = self._client().get("/").json()
        assert "oauth_protected_resource" not in body["endpoints"]
        assert "oauth2" not in body["mcp"]["authentication"]["schemes"]

        monkeypatch.setattr(index, "authkit_domain", lambda: "https://auth.example.com")
        body = self._client().get("/").json()
        assert body["endpoints"]["oauth_protected_resource"].endswith(
            "/.well-known/oauth-protected-resource/mcp"
        )
        auth = body["mcp"]["authentication"]
        assert auth["schemes"] == ["oauth2", "api_key"]
        assert auth["authorization_servers"] == ["https://auth.example.com"]

    def test_root_prefers_the_configured_public_host(self, monkeypatch):
        # Absolute URLs must match what OAuth binds tokens to, not the request
        # origin, which behind a proxy can be the internal hostname.
        monkeypatch.setattr(
            index.global_config, "MCP_PUBLIC_URL", "https://mcp.example.com/mcp"
        )
        body = self._client().get("/").json()
        assert body["mcp"]["url"] == "https://mcp.example.com/mcp"
        assert body["endpoints"]["health"] == "https://mcp.example.com/health"

    def test_endpoints_mcp_matches_mcp_url(self, monkeypatch):
        # A path-carrying public URL (a rewriting proxy in front) must not be
        # rebuilt from the origin, or one document states the endpoint twice
        # and disagrees with itself.
        monkeypatch.setattr(
            index.global_config, "MCP_PUBLIC_URL", "https://mcp.example.com/gateway/mcp"
        )
        body = self._client().get("/").json()
        assert body["endpoints"]["mcp"] == body["mcp"]["url"]
        assert body["mcp"]["url"] == "https://mcp.example.com/gateway/mcp"

    def test_explicit_html_rejection_gets_json(self):
        # `text/html;q=0` refuses HTML outright; handing it over anyway breaks
        # the client that was careful enough to say so.
        resp = self._client().get(
            "/", headers={"accept": "application/json, text/html;q=0"}
        )
        assert resp.headers["content-type"].startswith("application/json")

    def test_json_ranked_above_html_gets_json(self):
        resp = self._client().get(
            "/", headers={"accept": "text/html;q=0.8, application/json;q=0.9"}
        )
        assert resp.headers["content-type"].startswith("application/json")

    def test_browser_accept_header_still_gets_html(self):
        # The real header Chrome sends - HTML must still win it.
        resp = self._client().get(
            "/",
            headers={
                "accept": "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            },
        )
        assert resp.headers["content-type"].startswith("text/html")

    def test_root_serves_html_to_browsers(self):
        resp = self._client().get("/", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "/mcp" in resp.text
        # The copy-pasteable connect command is the point of the page.
        assert "claude mcp add --transport http" in resp.text
        # …and the command alone leaves you at a 401, so name the credential.
        assert "X-Api-Key" in resp.text
        # Self-contained: no external assets to block or leak the visit.
        assert "src=" not in resp.text

    def test_head_root_is_supported(self):
        # Uptime probes and link checkers send HEAD before GET.
        assert self._client().head("/").status_code == 200

    def test_root_stays_out_of_the_openapi_spec(self):
        assert "/" not in app.openapi()["paths"]


class TestNotFoundFallback(TestTemplate):
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

    def test_unrecognized_paths_get_no_suggestion(self):
        # No fuzzy matching: a wrong guess sends an agent down a dead path with
        # full confidence, which is worse than the endpoint map alone.
        body = self._client().get("/.well-known/security.txt").json()
        assert "did_you_mean" not in body["error"]["details"]

    def test_404_is_cors_readable(self):
        # The browser-based agent that probed a wrong path is who the hint is
        # for; without this it reads an opaque CORS failure instead.
        resp = self._client().get("/definitely-not-a-route")
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_fallback_does_not_shadow_the_mcp_mount(self):
        # /mcp is still FastMCP's; unauthenticated callers get the JSON-RPC 401
        # challenge, not the landing-page 404.
        resp = self._client().get("/mcp")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == -32001
        assert "www-authenticate" in resp.headers

    def test_mcp_lookalike_paths_get_the_landing_404(self):
        # /mcpfoo is not the transport, so it must not be answered with an auth
        # challenge implying one lives there. The auth middleware and the 404
        # handler share one segment-aware predicate; this proves they agree.
        resp = self._client().get("/mcpfoo")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_mcp_transport_surface_keeps_its_own_404(self):
        # Registration is by status code, so a 404 raised inside the transport
        # would land here. The transport owns that surface: a stale session must
        # not be answered with a landing document.
        assert index.is_mcp_path("/mcp") is True
        assert index.is_mcp_path("/mcp/session") is True
        assert index.is_mcp_path("/mcpfoo") is False

    def test_real_routes_are_untouched(self):
        assert self._client().get("/health").status_code == 200
