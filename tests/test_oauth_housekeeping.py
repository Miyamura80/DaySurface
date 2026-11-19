"""OAuth server housekeeping: purging expired codes and tokens, and its runner."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select, update

from api_server import runner
from api_server.oauth.storage import CODE_PURGE_GRACE, purge_expired
from common import global_config
from db.models.oauth import OAuthCode, OAuthToken
from tests.oauth_server_fixture import OAuthServerCase
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
