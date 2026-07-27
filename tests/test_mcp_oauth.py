"""Tests for OAuth 2.1 resource-server support on the /mcp transport.

Covers AuthKit access-token verification (signature, issuer, audience,
scope mapping), the RFC 9728 Protected Resource Metadata endpoints, the
``WWW-Authenticate`` discovery hint on 401, and an end-to-end MCP
``initialize`` with an OAuth Bearer token.
"""

import json
import time
from unittest.mock import patch

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api_server.auth.authkit_auth import (
    mcp_resource_url,
    resource_metadata_url,
    verify_authkit_token,
)
from api_server.routes import well_known
from api_server.server import app
from tests.test_template import TestTemplate

AUTHKIT_DOMAIN = "https://test-env.authkit.app"
RESOURCE = "https://mcp.example.com/mcp"


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, **overrides) -> str:
    payload = {
        "sub": "user-oauth",
        "aud": RESOURCE,
        "iss": AUTHKIT_DOMAIN,
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _patch_jwks(mock_jwks, public_key) -> None:
    class FakeSigningKey:
        key = public_key

    mock_jwks.return_value.get_signing_key_from_jwt.return_value = FakeSigningKey()


def _read_sse_first_message(response) -> dict:
    """Parse the first ``data:`` line from an MCP SSE response."""
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("no SSE data frame in response")


class TestAuthKitTokenVerification(TestTemplate):
    @patch("api_server.auth.authkit_auth.global_config")
    def test_no_domain_returns_none(self, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = None
        assert verify_authkit_token("anything") is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_valid_token(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, email="o@test.com")
        user = verify_authkit_token(token)
        assert user is not None
        assert user.user_id == "user-oauth"
        assert user.email == "o@test.com"
        assert user.scopes == ["*"]

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_scope_claim_mapped(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope="services:read services:execute")
        user = verify_authkit_token(token)
        assert user is not None
        assert user.scopes == ["services:read", "services:execute"]

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_empty_scope_grants_full_access(self, mock_jwks, mock_config):
        # An empty scope claim carries none of our authorization scopes, so the
        # interactive user who consented to the whole resource gets full access,
        # same as a token with no scope claim at all.
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope="")
        user = verify_authkit_token(token)
        assert user is not None
        assert user.scopes == ["*"]

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_identity_only_scopes_grant_full_access(self, mock_jwks, mock_config):
        # The production case: WorkOS AuthKit issues OIDC identity scopes that
        # are orthogonal to our services:* namespace. They must not down-scope
        # the consented interactive user to zero permissions.
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope="openid profile email offline_access")
        user = verify_authkit_token(token)
        assert user is not None
        assert user.scopes == ["*"]

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_misconfigured_namespace_scope_rejected(self, mock_jwks, mock_config):
        # A value that targets our namespace but does not resolve to a real
        # scope (typo / misconfiguration) must fail closed, not silently upgrade
        # the botched down-scoping to full access.
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope="services:exceute")
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_multi_colon_namespace_scope_rejected(self, mock_jwks, mock_config):
        # Extra colons must not let a namespace-targeting value slip past the
        # fail-closed check (services:foo:bar is still our namespace, invalid).
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope="services:foo:bar")
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_malformed_scope_claim_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, scope=123)
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_wrong_audience_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, aud="https://other.example.com/mcp")
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_wrong_issuer_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, iss="https://evil.example.com")
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_expired_token_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        token = _make_token(private_key, exp=int(time.time()) - 3600)
        assert verify_authkit_token(token) is None

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_missing_sub_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)

        payload = {
            "aud": RESOURCE,
            "iss": AUTHKIT_DOMAIN,
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, private_key, algorithm="RS256")
        assert verify_authkit_token(token) is None


class TestResourceUrls(TestTemplate):
    @patch("api_server.auth.authkit_auth.global_config")
    def test_resource_url_strips_trailing_slash(self, mock_config):
        mock_config.MCP_PUBLIC_URL = "https://mcp.example.com/mcp/"
        assert mcp_resource_url() == "https://mcp.example.com/mcp"

    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_url_is_path_form(self, mock_config):
        mock_config.MCP_PUBLIC_URL = RESOURCE
        assert resource_metadata_url() == (
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
        )

    @patch("api_server.auth.authkit_auth.global_config")
    def test_resource_url_default_without_config(self, mock_config):
        mock_config.MCP_PUBLIC_URL = None
        mock_config.server.port = 8080
        assert mcp_resource_url() == "http://localhost:8080/mcp"


class TestProtectedResourceMetadata(TestTemplate):
    def _client(self) -> TestClient:
        # No lifespan needed: these routes never reach the MCP sub-app.
        return TestClient(app)

    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_served_when_configured(self, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_config.MCP_PUBLIC_URL = RESOURCE
        for path in (
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ):
            resp = self._client().get(path)
            assert resp.status_code == 200, path
            body = resp.json()
            assert body["resource"] == RESOURCE
            assert body["authorization_servers"] == [AUTHKIT_DOMAIN]
            assert body["bearer_methods_supported"] == ["header"]

    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_404_when_unconfigured(self, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = None
        resp = self._client().get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 404


class TestAuthorizationServerMetadata(TestTemplate):
    """RFC 8414 metadata mirrored at the resource server's well-known path.

    Compatibility shim for clients/scanners that look for AS metadata on the
    resource server and do not follow the redirect to the authorization server.
    """

    _AS_DOC = {
        "issuer": AUTHKIT_DOMAIN,
        "authorization_endpoint": f"{AUTHKIT_DOMAIN}/oauth2/authorize",
        "token_endpoint": f"{AUTHKIT_DOMAIN}/oauth2/token",
    }

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        # Module-level cache is keyed by issuer; clear it so each test fetches.
        well_known._as_metadata_cache.clear()
        yield
        well_known._as_metadata_cache.clear()

    def _client(self) -> TestClient:
        return TestClient(app)

    @patch("api_server.routes.well_known.httpx.get")
    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_served_when_configured(self, mock_config, mock_get):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_get.return_value.json.return_value = self._AS_DOC
        mock_get.return_value.raise_for_status.return_value = None

        for path in (
            "/.well-known/oauth-authorization-server/mcp",
            "/.well-known/oauth-authorization-server",
        ):
            resp = self._client().get(path)
            assert resp.status_code == 200, path
            body = resp.json()
            assert body["issuer"] == AUTHKIT_DOMAIN
            assert (
                body["authorization_endpoint"] == f"{AUTHKIT_DOMAIN}/oauth2/authorize"
            )
            assert body["token_endpoint"] == f"{AUTHKIT_DOMAIN}/oauth2/token"
            assert resp.headers["access-control-allow-origin"] == "*"

    @patch("api_server.routes.well_known.httpx.get")
    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_cached_across_requests(self, mock_config, mock_get):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_get.return_value.json.return_value = self._AS_DOC
        mock_get.return_value.raise_for_status.return_value = None

        client = self._client()
        client.get("/.well-known/oauth-authorization-server")
        client.get("/.well-known/oauth-authorization-server")
        assert mock_get.call_count == 1

    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_404_when_unconfigured(self, mock_config):
        mock_config.WORKOS_AUTHKIT_DOMAIN = None
        resp = self._client().get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 404

    @patch("api_server.routes.well_known.httpx.get")
    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_502_on_upstream_error(self, mock_config, mock_get):
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_get.side_effect = httpx.HTTPError("boom")
        resp = self._client().get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 502

    @patch("api_server.routes.well_known.httpx.get")
    @patch("api_server.auth.authkit_auth.global_config")
    def test_metadata_502_on_non_json_upstream(self, mock_config, mock_get):
        # A non-JSON upstream body must be treated as a fetch failure (502),
        # not crash the endpoint with a 500.
        mock_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.side_effect = ValueError("not json")
        resp = self._client().get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 502


class TestUnauthorizedDiscoveryHint(TestTemplate):
    def _post_mcp_unauthenticated(self):
        # No lifespan: mcp_auth short-circuits before the MCP sub-app.
        client = TestClient(app)
        return client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.middleware.mcp_auth.global_config")
    def test_401_advertises_resource_metadata(self, mock_mw_config, mock_ak_config):
        mock_mw_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_ak_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_ak_config.MCP_PUBLIC_URL = RESOURCE

        resp = self._post_mcp_unauthenticated()
        assert resp.status_code == 401
        challenge = resp.headers["www-authenticate"]
        assert challenge.startswith('Bearer realm="mcp"')
        assert (
            'resource_metadata="https://mcp.example.com'
            '/.well-known/oauth-protected-resource/mcp"' in challenge
        )

    @patch("api_server.middleware.mcp_auth.global_config")
    def test_401_plain_challenge_when_unconfigured(self, mock_mw_config):
        mock_mw_config.WORKOS_AUTHKIT_DOMAIN = None
        resp = self._post_mcp_unauthenticated()
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == 'Bearer realm="mcp"'


class TestMCPInitializeWithOAuthToken(TestTemplate):
    @patch("api_server.auth.authkit_auth.global_config")
    @patch("api_server.middleware.mcp_auth.global_config")
    @patch("api_server.auth.authkit_auth._get_jwks_client")
    def test_initialize_with_authkit_bearer(
        self, mock_jwks, mock_mw_config, mock_ak_config
    ):
        mock_mw_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_mw_config.WORKOS_CLIENT_ID = None
        mock_ak_config.WORKOS_AUTHKIT_DOMAIN = AUTHKIT_DOMAIN
        mock_ak_config.MCP_PUBLIC_URL = RESOURCE

        private_key, public_key = _generate_rsa_keypair()
        _patch_jwks(mock_jwks, public_key)
        token = _make_token(private_key)

        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "127.0.0.1:8080",
                    "Authorization": f"Bearer {token}",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
        assert resp.status_code == 200, resp.text
        msg = _read_sse_first_message(resp)
        assert msg["result"]["serverInfo"]["name"] == "mymcp"
