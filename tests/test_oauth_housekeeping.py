"""OAuth server housekeeping: purging, startup checks, authkit inertness."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import api_server.oauth as oauth_server
from api_server import runner
from api_server.middleware.mcp_auth import _authenticate
from api_server.oauth.storage import CODE_PURGE_GRACE, purge_expired
from api_server.server import app
from common import global_config
from db.models.oauth import OAuthCode, OAuthToken
from tests.oauth_server_fixture import OAuthServerCase, bearer_scope
from tests.test_template import TestTemplate


class TestPurgeExpired(OAuthServerCase):
    def _grant(self) -> str:
        """Sign in and exchange once; return the token family id."""
        code, verifier = self._sign_in()
        self._exchange(code, verifier)
        with self.Session() as session:
            families = {row.family_id for row in session.scalars(select(OAuthToken))}
        (family,) = families - getattr(self, "_seen", set())
        self._seen = families
        return family

    def test_removes_only_rows_that_can_no_longer_be_used(self):
        old_family = self._grant()
        live_family = self._grant()
        now = datetime.now(UTC)
        with self.Session() as session:
            session.execute(
                update(OAuthToken)
                .where(OAuthToken.family_id == old_family)
                .values(family_expires_at=now - timedelta(seconds=1))
            )
            # The old grant's code is past its grace; the live one's is expired
            # but still within it, so a replay can still revoke its family.
            session.execute(
                update(OAuthCode)
                .where(OAuthCode.code_hash == old_family)
                .values(expires_at=now - CODE_PURGE_GRACE - timedelta(minutes=1))
            )
            session.execute(
                update(OAuthCode)
                .where(OAuthCode.code_hash == live_family)
                .values(expires_at=now - timedelta(minutes=10))
            )
            session.commit()

        assert purge_expired() == 3  # one code, an access and a refresh token

        with self.Session() as session:
            families = {row.family_id for row in session.scalars(select(OAuthToken))}
            codes = set(session.scalars(select(OAuthCode.code_hash)))
        assert families == {live_family}
        assert codes == {live_family}

    def test_nothing_to_purge(self):
        self._grant()
        assert purge_expired() == 0


class TestOAuthPurgeRunner(TestTemplate):
    def _run_lifespan(self, mode: str) -> MagicMock:
        purge = MagicMock(return_value=0)

        async def scenario() -> None:
            async with runner.runner_lifespan(None):
                await asyncio.sleep(0.05)

        with (
            patch.object(global_config, "MCP_AUTH_MODE", mode),
            patch.object(runner, "runner_enabled", return_value=False),
            patch("api_server.oauth.storage.purge_expired", purge),
        ):
            asyncio.run(scenario())
        return purge

    def test_self_mode_purges(self):
        assert self._run_lifespan("self").call_count == 1

    def test_authkit_mode_does_not_purge(self):
        self._run_lifespan("authkit").assert_not_called()


class TestStartupRequiresWorkOS(OAuthServerCase):
    @pytest.mark.parametrize("name", ["WORKOS_CLIENT_ID", "WORKOS_API_KEY"])
    def test_self_mode_without_workos_fails_fast(self, name):
        # In every environment, development included: sign-in cannot work.
        with (
            patch.object(global_config, name, None),
            pytest.raises(RuntimeError, match=name),
        ):
            oauth_server.create_sdk_routes()


class TestAuthkitModeIsInert(TestTemplate):
    """The default MCP_AUTH_MODE=authkit leaves the built-in OAuth server off."""

    _OAUTH_PATHS = ("/authorize", "/token", "/revoke", "/oauth/consent")

    def test_default_mode_is_authkit(self):
        assert type(global_config).model_fields["MCP_AUTH_MODE"].default == "authkit"
        assert global_config.MCP_AUTH_MODE == "authkit"

    def test_oauth_endpoints_are_not_routed(self):
        routed = {getattr(route, "path", None) for route in app.router.routes}
        assert routed.isdisjoint(self._OAUTH_PATHS)
        client = TestClient(app)
        for path in self._OAUTH_PATHS:
            assert client.get(path).status_code == 404, path
            assert client.post(path).status_code == 404, path

    def test_bearer_tokens_go_to_authkit(self):
        authkit_user = MagicMock(
            user_id="u-authkit", email="u@example.com", scopes=["services:execute"]
        )
        with (
            patch(
                "api_server.middleware.mcp_auth.verify_authkit_token",
                return_value=authkit_user,
            ) as authkit,
            patch("api_server.middleware.mcp_auth.lookup_access_token") as builtin,
        ):
            user = _authenticate(bearer_scope("eyJ.some.jwt"))
        authkit.assert_called_once_with("eyJ.some.jwt")
        builtin.assert_not_called()
        assert user is not None
        assert user.user_id == "u-authkit"
        assert user.auth_method == "oauth"
