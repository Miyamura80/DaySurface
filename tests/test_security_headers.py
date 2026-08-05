"""Security response headers and session-cookie flags (OWASP ASVS V14.4, V3.4).

Both findings here are what an unauthenticated DAST scan reports first: a
response with no ``Content-Security-Policy``/``X-Frame-Options``/HSTS, and a
session cookie set without ``Secure``. The tests assert the fix on the surfaces
that actually differ - a plain FastAPI route, the FastMCP mount at ``/mcp``,
and the paths the mount answers on FastAPI's behalf - because those travel
through three different response paths.
"""

import base64
import hashlib
import re
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from api_server.auth.api_key_auth import create_api_key
from api_server.middleware.security_headers import SecurityHeadersMiddleware
from api_server.server import SessionCookiePolicy, app, session_cookie_policy
from common import global_config
from db import engine as db_engine
from db.base import Base
from tests.test_template import TestTemplate

_PROTOCOL_VERSION = "2025-03-26"

# The headers a DAST scan looks for, each with the assertion that the value is
# actually a hardening one rather than a placeholder.
_EXPECTED = {
    "content-security-policy": lambda v: "default-src 'none'" in v,
    "strict-transport-security": lambda v: "max-age=" in v and "includeSubDomains" in v,
    "x-content-type-options": lambda v: v == "nosniff",
    "x-frame-options": lambda v: v == "DENY",
    "referrer-policy": lambda v: v == "no-referrer",
    "permissions-policy": lambda v: "geolocation=()" in v and "camera=()" in v,
    "x-permitted-cross-domain-policies": lambda v: v == "none",
}


def _assert_secure(headers, *, csp_check=None) -> None:
    """Assert every required header is present and meaningfully set."""
    for name, is_valid in _EXPECTED.items():
        assert name in headers, f"missing {name}"
        value = headers[name]
        check = (
            csp_check if (name == "content-security-policy" and csp_check) else is_valid
        )
        assert check(value), f"{name} has a non-hardening value: {value!r}"


@contextmanager
def _patch_db():
    """Wire an in-memory SQLite into db.engine for the duration of the block."""
    orig_engine = db_engine._engine
    orig_session = db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    try:
        yield session_factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


class TestSecurityHeaders(TestTemplate):
    def test_headers_on_a_normal_route(self):
        # /health is the endpoint an uptime probe and a scanner both hit first.
        resp = TestClient(app).get("/health")
        assert resp.status_code < 500
        _assert_secure(resp.headers)

    def test_headers_on_the_mcp_mount(self):
        """The FastMCP mount answers /mcp - it must not bypass the middleware.

        `mount_mcp_server` registers the sub-app with `app.mount("/", ...)`, a
        route on `app.router`; `add_middleware` wraps the router, so responses
        produced *inside* the mount still travel back out through it. Asserted
        on a real 200 from the transport (an `initialize` handshake), not on
        the MCPAuthMiddleware 401, which is produced before the mount is
        reached and so would prove nothing about the mount.
        """
        with patch("api_server.middleware.mcp_auth.global_config") as mock_config:
            mock_config.WORKOS_CLIENT_ID = None
            mock_config.WORKOS_AUTHKIT_DOMAIN = None
            with _patch_db() as session_factory:
                with session_factory() as s:
                    raw_key, _ = create_api_key(s, user_id="u-sec-hdr", scopes=["*"])
                with TestClient(app) as client:
                    resp = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Host": "127.0.0.1:8080",
                            "X-API-KEY": raw_key,
                            "MCP-Protocol-Version": _PROTOCOL_VERSION,
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": _PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": {"name": "sec-hdr", "version": "0"},
                            },
                        },
                    )
        assert resp.status_code == 200, resp.text
        _assert_secure(resp.headers)

    def test_headers_on_the_unauthenticated_mcp_challenge(self):
        # The 401 is written straight to `send` by MCPAuthMiddleware; an
        # anonymous scanner sees this response and nothing else.
        resp = TestClient(app).post("/mcp", json={})
        assert resp.status_code == 401
        _assert_secure(resp.headers)

    def test_headers_on_a_path_only_the_mount_answers(self):
        # The mount sits at "/", so this 404 is generated inside the sub-app.
        resp = TestClient(app).get("/no-such-path")
        assert resp.status_code == 404
        _assert_secure(resp.headers)

    def test_csp_permits_the_landing_page_inline_style(self):
        """The CSP must carry a hash for the style block it actually serves.

        `index.py` hashes the markup it is about to send and sets the header on
        that response; the middleware's setdefault leaves it alone. Editing
        `_landing.html` therefore cannot desync the policy - but this pins the
        wiring, so dropping the route's header fails here rather than silently
        shipping an unstyled page.
        """
        resp = TestClient(app).get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

        blocks = re.findall(r"<style[^>]*>(.*?)</style>", resp.text, re.DOTALL)
        assert blocks, "landing page no longer has an inline style block"
        csp = resp.headers["content-security-policy"]
        for block in blocks:
            digest = base64.b64encode(hashlib.sha256(block.encode()).digest()).decode()
            assert f"'sha256-{digest}'" in csp, (
                "landing page inline style is not allowed by the CSP"
            )
        # The hash is the whole point: it must not be backed by unsafe-inline.
        assert "'unsafe-inline'" not in csp

    def test_csp_forbids_scripts_and_framing_on_the_api_surface(self):
        csp = TestClient(app).get("/health").headers["content-security-policy"]
        assert "script-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp

    def test_docs_get_a_scoped_exception(self):
        """Swagger/ReDoc need a looser policy; it must reach only those paths.

        Asserted on live responses rather than on the path predicate, so the
        selection *wiring* is covered: reading the wrong scope key, or a
        root_path that shifts the served path, would serve Swagger under
        `script-src 'none'` - a blank page that a pure-function test cannot see.
        """
        client = TestClient(app)
        docs_csp = client.get("/docs").headers["content-security-policy"]
        assert "https://cdn.jsdelivr.net" in docs_csp
        assert "frame-ancestors 'none'" in docs_csp

        assert (
            "https://cdn.jsdelivr.net"
            in client.get("/redoc").headers["content-security-policy"]
        )

        # Neighbours that merely look like the docs must keep the default.
        for path in ("/health", "/docsearch", "/docs/not-a-real-page"):
            assert (
                "'unsafe-inline'"
                not in client.get(path).headers["content-security-policy"]
            ), path

    def test_a_route_may_override_a_security_header(self):
        """setdefault semantics, asserted against a route that actually sets one.

        Load-bearing rather than cosmetic: `index.py` relies on it to attach the
        landing page's style hash. Replacing setdefault with assignment must
        fail this test.
        """

        async def opinionated(request: Request) -> PlainTextResponse:
            return PlainTextResponse(
                "ok",
                headers={
                    "Content-Security-Policy": "default-src 'self'",
                    "X-Frame-Options": "SAMEORIGIN",
                },
            )

        probe = Starlette(routes=[Route("/", opinionated)])
        probe.add_middleware(SecurityHeadersMiddleware)
        resp = TestClient(probe).get("/")

        assert resp.headers["content-security-policy"] == "default-src 'self'"
        assert resp.headers["x-frame-options"] == "SAMEORIGIN"
        # Headers the route did not claim are still applied.
        assert resp.headers["x-content-type-options"] == "nosniff"
        # ...and exactly once - a duplicate CSP would be intersected by the
        # browser, silently reinstating the stricter default.
        assert len(resp.headers.get_list("content-security-policy")) == 1

    def test_the_landing_page_owns_its_csp_not_the_middleware(self):
        # The style-hash exception must be scoped to the document that needs
        # it; a second HTML route must not widen style-src for the whole host.
        assert (
            "sha256-"
            not in TestClient(app).get("/health").headers["content-security-policy"]
        )


class TestSessionCookieFlags(TestTemplate):
    def _cookie(self, policy: SessionCookiePolicy) -> str:
        """Set-Cookie emitted by SessionMiddleware configured with *policy*."""

        async def write_session(request: Request) -> PlainTextResponse:
            request.session["k"] = "v"
            return PlainTextResponse("ok")

        probe = Starlette(routes=[Route("/", write_session)])
        probe.add_middleware(SessionMiddleware, secret_key="unit-test-secret", **policy)
        resp = TestClient(probe).get("/")
        assert resp.status_code == 200
        return resp.headers["set-cookie"]

    def test_production_session_cookie_is_secure(self):
        with patch.object(global_config, "DEV_ENV", "prod"):
            policy = session_cookie_policy()
        assert policy["https_only"] is True
        assert policy["same_site"] == "lax"

        # Cookie attribute names are case-insensitive (RFC 6265 §5.2) and
        # Starlette emits them lowercased; compare on a folded copy.
        cookie = self._cookie(policy).lower()
        assert "secure" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    def test_unknown_dev_env_fails_secure(self):
        # Staging, a typo, or an unset value must not be read as development.
        for value in ("staging", "", "production", None):
            with patch.object(global_config, "DEV_ENV", value):
                assert global_config.is_dev is False, value
                assert session_cookie_policy()["https_only"] is True, value

    def test_local_development_stays_on_plain_http(self):
        for value in ("dev", "local", "DEV"):
            with patch.object(global_config, "DEV_ENV", value):
                assert global_config.is_dev is True, value
                assert session_cookie_policy()["https_only"] is False, value

    def test_the_app_registers_session_middleware_with_the_policy(self):
        entries = [m for m in app.user_middleware if m.cls is SessionMiddleware]
        assert len(entries) == 1, "expected exactly one SessionMiddleware"
        kwargs = entries[0].kwargs
        assert kwargs["same_site"] == "lax"
        # Under the test config DEV_ENV is `dev`, so this mirrors the gate
        # rather than asserting True - the prod behaviour is pinned above.
        assert kwargs["https_only"] == session_cookie_policy()["https_only"]
