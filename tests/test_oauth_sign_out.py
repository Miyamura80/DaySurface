"""Signing out of DaySurface's own OAuth server."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from api_server.middleware.mcp_auth import _authenticate
from common import global_config
from db.models.oauth import OAuthCode, lock_user
from models.account import SignOutInput
from services import ClientRefusalError
from services.account import sign_out_svc
from tests.oauth_server_fixture import (
    USER_ID,
    WORKOS_SESSION,
    OAuthServerCase,
    bearer_scope,
    consent_form,
    pkce,
)
from tests.test_template import TestTemplate


class TestSignOut(OAuthServerCase):
    # -- sign-out --------------------------------------------------------------

    def test_sign_out_revokes_every_token_and_ends_the_workos_session(self):
        tokens = []
        for _ in range(2):  # two connected apps
            code, verifier = self._sign_in()
            tokens.append(self._exchange(code, verifier))
        fake = MagicMock()
        fake.__enter__.return_value.post.return_value = MagicMock(
            is_success=True, status_code=200
        )
        with patch.object(sign_out_svc.httpx, "Client", return_value=fake):
            result = sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))
        assert result.tokens_revoked == 4  # access + refresh, twice
        assert result.sign_in_sessions_ended == 1
        fake.__enter__.return_value.post.assert_called_once_with(
            sign_out_svc.WORKOS_REVOKE_SESSION_URL, json={"session_id": WORKOS_SESSION}
        )
        for pair in tokens:
            assert _authenticate(bearer_scope(pair["access_token"])) is None
            refreshed = self._token(
                grant_type="refresh_token", refresh_token=pair["refresh_token"]
            )
            assert refreshed["status"] == 400

    def test_sign_out_refuses_when_authkit_issues_the_tokens(self):
        with (
            patch.object(global_config, "MCP_AUTH_MODE", "authkit"),
            pytest.raises(ClientRefusalError),
        ):
            sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))

    # -- sign-out cuts off sign-ins that are under way -------------------------

    def test_sign_out_spends_codes_not_yet_exchanged(self):
        code, verifier = self._sign_in()
        with patch.object(sign_out_svc, "_end_workos_sessions", return_value=0):
            sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))
        assert self._exchange(code, verifier)["status"] == 400
        with self.Session() as session:
            assert all(row.used_at for row in session.scalars(select(OAuthCode)))

    def test_sign_out_during_sign_in_refuses_the_callback(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        workos = self.http.post("/oauth/consent", data=consent_form(page.text, "allow"))
        state = parse_qs(urlsplit(workos.headers["location"]).query)["state"][0]
        with patch.object(sign_out_svc, "_end_workos_sessions", return_value=0):
            sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))
        back = self.http.get(
            "/oauth/workos/callback", params={"code": "wc", "state": state}
        )
        assert back.status_code == 303
        query = parse_qs(urlsplit(back.headers["location"]).query)
        assert query["error"] == ["access_denied"]
        assert "code" not in query

    def test_signing_in_again_after_sign_out_works(self):
        with patch.object(sign_out_svc, "_end_workos_sessions", return_value=0):
            sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))
        # Straight away, within the same second: the sign-in's start is kept
        # to the microsecond, so it is not mistaken for one begun earlier.
        code, verifier = self._sign_in()
        assert self._exchange(code, verifier)["status"] == 200

    def test_sign_out_ends_the_session_of_a_code_not_yet_exchanged(self):
        self._sign_in()  # code issued, never exchanged
        fake = MagicMock()
        fake.__enter__.return_value.post.return_value = MagicMock(
            is_success=True, status_code=200
        )
        with patch.object(sign_out_svc.httpx, "Client", return_value=fake):
            result = sign_out_svc.sign_out(SignOutInput(user_id=USER_ID))
        assert result.tokens_revoked == 0
        assert result.sign_in_sessions_ended == 1
        fake.__enter__.return_value.post.assert_called_once_with(
            sign_out_svc.WORKOS_REVOKE_SESSION_URL, json={"session_id": WORKOS_SESSION}
        )


class TestUserLock(TestTemplate):
    def test_postgres_takes_a_transaction_advisory_lock(self):
        session = MagicMock()
        session.get_bind.return_value.dialect.name = "postgresql"
        lock_user(session, USER_ID)
        statement = str(session.execute.call_args.args[0])
        assert "pg_advisory_xact_lock" in statement

    def test_other_databases_take_nothing(self):
        session = MagicMock()
        session.get_bind.return_value.dialect.name = "sqlite"
        lock_user(session, USER_ID)
        session.execute.assert_not_called()
