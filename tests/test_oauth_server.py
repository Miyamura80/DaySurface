"""DaySurface's own OAuth server: metadata, token lifecycle and the browser leg."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

import api_server.oauth as oauth_server
from api_server.middleware.mcp_auth import _authenticate
from api_server.oauth import flow
from common import global_config
from db.models.oauth import OAuthToken
from tests.oauth_server_fixture import (
    CLIENT_ID,
    ISSUER,
    MCP_URL,
    REDIRECT_URI,
    USER_ID,
    OAuthServerCase,
    bearer_scope,
    client_document,
    consent_form,
    pkce,
)


class TestOAuthServer(OAuthServerCase):
    # -- metadata ------------------------------------------------------------

    def test_metadata_advertises_daysurface_as_the_authorization_server(self):
        prm = self.http.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["resource"] == MCP_URL
        assert prm["authorization_servers"] == [ISSUER]
        meta = self.http.get("/.well-known/oauth-authorization-server").json()
        assert meta["issuer"] == ISSUER  # exact, no trailing slash (RFC 9207)
        assert meta["authorization_endpoint"] == f"{ISSUER}/authorize"
        assert meta["token_endpoint"] == f"{ISSUER}/token"
        assert meta["revocation_endpoint"] == f"{ISSUER}/revoke"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert meta["client_id_metadata_document_supported"] is True
        assert meta["authorization_response_iss_parameter_supported"] is True
        assert "registration_endpoint" not in meta
        # Public clients only: nothing to authenticate with.
        assert meta["token_endpoint_auth_methods_supported"] == ["none"]
        assert meta["revocation_endpoint_auth_methods_supported"] == ["none"]

    # -- full flow -------------------------------------------------------------

    def test_full_flow_issues_a_token_that_opens_mcp(self):
        code, verifier = self._sign_in()
        tokens = self._exchange(code, verifier)
        assert tokens["status"] == 200
        assert tokens["token_type"].lower() == "bearer"
        assert tokens["access_token"].startswith("dsa_")
        assert tokens["refresh_token"].startswith("dsr_")
        user = _authenticate(bearer_scope(tokens["access_token"]))
        assert user is not None
        assert user.user_id == USER_ID
        assert user.auth_method == "oauth"

    def test_tokens_are_stored_only_as_hashes(self):
        code, verifier = self._sign_in()
        tokens = self._exchange(code, verifier)
        with self.Session() as session:
            stored = {row.token_hash for row in session.scalars(select(OAuthToken))}
        assert tokens["access_token"] not in stored
        assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored

    def test_wrong_pkce_verifier_is_refused(self):
        code, _ = self._sign_in()
        assert self._exchange(code, "wrong-verifier-" + "x" * 40)["status"] == 400

    def test_replayed_code_is_refused_and_revokes_what_it_issued(self):
        code, verifier = self._sign_in()
        first = self._exchange(code, verifier)
        assert first["status"] == 200
        replay = self._exchange(code, verifier)
        assert replay["status"] == 400
        assert replay["error"] == "invalid_grant"
        assert _authenticate(bearer_scope(first["access_token"])) is None

    def test_refresh_rotates_and_reuse_revokes_the_family(self):
        code, verifier = self._sign_in()
        first = self._exchange(code, verifier)
        second = self._token(
            grant_type="refresh_token", refresh_token=first["refresh_token"]
        )
        assert second["status"] == 200
        assert second["refresh_token"] != first["refresh_token"]
        assert _authenticate(bearer_scope(second["access_token"])) is not None
        # Presenting the already-used refresh token again: treated as theft.
        reuse = self._token(
            grant_type="refresh_token", refresh_token=first["refresh_token"]
        )
        assert reuse["status"] == 400
        assert _authenticate(bearer_scope(second["access_token"])) is None
        rotated = self._token(
            grant_type="refresh_token", refresh_token=second["refresh_token"]
        )
        assert rotated["status"] == 400

    def test_revocation_endpoint_revokes_access_and_refresh(self):
        code, verifier = self._sign_in()
        tokens = self._exchange(code, verifier)
        response = self.http.post(
            "/revoke", data={"token": tokens["access_token"], "client_id": CLIENT_ID}
        )
        assert response.status_code == 200
        assert _authenticate(bearer_scope(tokens["access_token"])) is None
        refresh = self._token(
            grant_type="refresh_token", refresh_token=tokens["refresh_token"]
        )
        assert refresh["status"] == 400

    # -- browser-leg protections ---------------------------------------------

    def test_consent_post_without_the_browser_cookie_is_refused(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        self.http.cookies.clear()
        response = self.http.post(
            "/oauth/consent", data=consent_form(page.text, "allow")
        )
        assert response.status_code == 400
        assert "location" not in response.headers

    def test_callback_in_another_browser_is_refused(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        workos = self.http.post("/oauth/consent", data=consent_form(page.text, "allow"))
        state = parse_qs(urlsplit(workos.headers["location"]).query)["state"][0]
        self.http.cookies.clear()
        response = self.http.get(
            "/oauth/workos/callback", params={"code": "wc", "state": state}
        )
        assert response.status_code == 400

    def test_deny_returns_access_denied_with_issuer(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        response = self.http.post(
            "/oauth/consent", data=consent_form(page.text, "deny")
        )
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["error"] == ["access_denied"]
        assert query["iss"] == [ISSUER]

    def test_consent_page_names_the_client_and_redirect_host_escaped(self):
        self.document = client_document(client_name="<script>alert(1)</script>")
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
        assert "client.test.example" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

    def test_token_for_another_resource_is_refused(self):
        _, challenge = pkce()
        response = self.http.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "s",
                "resource": "https://other.example/mcp",
            },
        )
        assert "/oauth/consent" not in response.headers.get("location", "")

    def test_unregistered_redirect_uri_is_refused(self):
        _, challenge = pkce()
        response = self.http.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": "https://attacker.example/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": MCP_URL,
            },
        )
        assert response.status_code == 400
        assert "attacker.example" not in response.headers.get("location", "")

    # -- scopes and grant lifetime ---------------------------------------------

    def test_refresh_cannot_widen_the_granted_scopes(self):
        code, verifier = self._sign_in(scope="services:read")
        tokens = self._exchange(code, verifier)
        assert tokens["status"] == 200
        for wider in ("services:read services:execute", "openid"):
            # "openid" drops the last DaySurface scope, which means full access.
            refused = self._token(
                grant_type="refresh_token",
                refresh_token=tokens["refresh_token"],
                scope=wider,
            )
            assert refused["status"] == 400, wider
            assert refused["error"] == "invalid_scope"
        narrower = self._token(
            grant_type="refresh_token",
            refresh_token=tokens["refresh_token"],
            scope="services:read",
        )
        assert narrower["status"] == 200

    def test_refreshing_never_extends_the_grant_past_its_cap(self):
        code, verifier = self._sign_in()
        tokens = self._exchange(code, verifier)
        cap = datetime.now(UTC) + timedelta(minutes=10)
        with self.Session() as session:
            for row in session.scalars(select(OAuthToken)):
                row.family_expires_at = cap
            session.commit()
        refreshed = self._token(
            grant_type="refresh_token", refresh_token=tokens["refresh_token"]
        )
        assert refreshed["status"] == 200
        assert refreshed["expires_in"] <= 600
        with self.Session() as session:
            newest = max(
                session.scalars(select(OAuthToken)), key=lambda row: row.created_at
            )
            assert newest.family_expires_at.replace(tzinfo=UTC) == cap

    # -- iss on every redirect back to the client ------------------------------

    def test_sdk_error_redirects_carry_the_issuer(self):
        _, challenge = pkce()
        location = self._authorize(challenge, scope="not-a-scope")
        query = parse_qs(urlsplit(location).query)
        assert location.startswith(REDIRECT_URI)
        assert query["error"] == ["invalid_scope"]
        assert query["iss"] == [ISSUER]

    def test_consent_redirect_is_left_alone(self):
        _, challenge = pkce()
        location = self._authorize(challenge)
        assert "iss" not in parse_qs(urlsplit(location).query)

    def test_malformed_code_challenge_is_refused(self):
        for challenge in ("short", "x" * 129, "a" * 42 + "!"):
            location = self._authorize(challenge)
            query = parse_qs(urlsplit(location).query)
            assert location.startswith(REDIRECT_URI), challenge
            assert query["error"] == ["invalid_request"]
            assert query["iss"] == [ISSUER]

    # -- browser binding ---------------------------------------------------------

    def test_non_ascii_nonce_is_refused_not_a_crash(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        form = {**consent_form(page.text, "allow"), "nonce": "\u00f1" * 32}
        response = self.http.post("/oauth/consent", data=form)
        assert response.status_code == 400

    def test_production_cookie_is_host_prefixed(self):
        _, challenge = pkce()
        consent_url = self._authorize(challenge)
        with patch.object(flow, "_secure_cookies", return_value=True):
            page = self.http.get(consent_url)
        cookie = page.headers["set-cookie"]
        assert cookie.startswith("__Host-ds_oauth_")
        for attribute in ("Secure", "HttpOnly", "Path=/", "SameSite=lax"):
            assert attribute in cookie
        assert "Domain=" not in cookie

    def test_self_mode_refuses_the_placeholder_secret_outside_development(self):
        with (
            patch.object(
                global_config, "SESSION_SECRET_KEY", "change-me-in-production"
            ),
            patch.object(
                type(global_config),
                "is_dev",
                new_callable=PropertyMock,
                return_value=False,
            ),
            pytest.raises(RuntimeError),
        ):
            oauth_server.create_sdk_routes()

    def test_mcp_rejects_foreign_bearer_tokens_in_self_mode(self):
        with patch("api_server.middleware.mcp_auth.verify_authkit_token") as authkit:
            assert _authenticate(bearer_scope("eyJ.some.jwt")) is None
        authkit.assert_not_called()
