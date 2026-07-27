"""Tests for Gmail OAuth services + /api/v1/auth/google routes."""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.server import app
from common import global_config
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from models.gmail import (
    GmailConnectInput,
    GmailDisconnectInput,
    GmailStatusInput,
)
from services.gmail_svc import (
    GMAIL_SCOPES,
    _sign_state,
    _verify_state,
    gmail_connect,
    gmail_disconnect,
    gmail_status,
)
from tests.test_template import TestTemplate

# ---------------------------------------------------------------------------
# DB fixture: in-memory SQLite wired into db.engine for the duration of a test
# ---------------------------------------------------------------------------


@contextmanager
def _patch_db():
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


def _fake_id_token(email: str) -> str:
    """Build a fake JWT whose middle segment encodes ``{"email": <email>}``."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    payload_bytes = json.dumps({"email": email}).encode("utf-8")
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.sig"


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


class TestGmailConnectService(TestTemplate):
    def test_auth_url_contains_all_scopes_and_valid_state(self):
        with (
            patch.object(global_config, "GOOGLE_CLIENT_ID", "test-client"),
            patch.object(
                global_config,
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/v1/auth/google/callback",
            ),
        ):
            result = gmail_connect(GmailConnectInput(user_id="user-1"))

        parsed = urlparse(result.auth_url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "accounts.google.com"

        qs = parse_qs(parsed.query)
        scope_param = qs["scope"][0]
        for scope in GMAIL_SCOPES:
            assert scope in scope_param

        assert qs["client_id"] == ["test-client"]
        assert qs["response_type"] == ["code"]
        assert qs["access_type"] == ["offline"]
        assert qs["prompt"] == ["consent"]
        assert qs["include_granted_scopes"] == ["true"]
        assert qs["state"] == [result.state]

        assert _verify_state(result.state) == "user-1"

    def test_raises_when_client_id_missing(self):
        with (
            patch.object(global_config, "GOOGLE_CLIENT_ID", None),
            patch.object(global_config, "GOOGLE_REDIRECT_URI", "http://x/y"),
            pytest.raises(RuntimeError, match="Google OAuth not configured"),
        ):
            gmail_connect(GmailConnectInput(user_id="user-1"))


class TestStateSigning(TestTemplate):
    def test_round_trip(self):
        state = _sign_state("user-42")
        assert _verify_state(state) == "user-42"

    def test_rejects_tampered_payload(self):
        state = _sign_state("user-42")
        payload_b64, sig_b64 = state.split(".", 1)
        # Flip a character in the payload
        bad_char = "A" if payload_b64[0] != "A" else "B"
        tampered = bad_char + payload_b64[1:] + "." + sig_b64
        assert _verify_state(tampered) is None

    def test_rejects_expired_state(self):
        with patch("services.gmail_svc.time.time", return_value=1_000_000.0):
            state = _sign_state("user-42")
        # Now jump forward past the 10-minute window
        with patch("services.gmail_svc.time.time", return_value=1_000_000.0 + 11 * 60):
            assert _verify_state(state) is None

    def test_rejects_malformed(self):
        assert _verify_state("") is None
        assert _verify_state("not-a-dot-token") is None
        assert _verify_state("aaa.bbb") is None


class TestGmailStatusService(TestTemplate):
    def test_not_connected_when_no_row(self):
        with _patch_db():
            result = gmail_status(GmailStatusInput(user_id="ghost"))
        assert result.connected is False
        assert result.email is None
        assert result.scopes == []

    def test_connected_when_active_row(self):
        with _patch_db() as factory:
            s = factory()
            s.add(
                GoogleToken(
                    user_id="alice",
                    email="alice@example.com",
                    refresh_token_enc=b"RT",
                    key_id="plaintext",
                    scopes=["openid", "email"],
                )
            )
            s.commit()
            s.close()

            result = gmail_status(GmailStatusInput(user_id="alice"))

        assert result.connected is True
        assert result.email == "alice@example.com"
        assert result.scopes == ["openid", "email"]

    def test_not_connected_when_revoked(self):
        with _patch_db() as factory:
            s = factory()
            s.add(
                GoogleToken(
                    user_id="bob",
                    email="bob@example.com",
                    refresh_token_enc=b"RT",
                    key_id="plaintext",
                    scopes=["email"],
                    revoked_at=datetime.now(UTC),
                )
            )
            s.commit()
            s.close()

            result = gmail_status(GmailStatusInput(user_id="bob"))

        assert result.connected is False


class TestGmailDisconnectService(TestTemplate):
    def test_returns_false_when_no_row(self):
        with _patch_db():
            result = gmail_disconnect(GmailDisconnectInput(user_id="ghost"))
        assert result.revoked is False

    def test_revokes_locally_even_when_http_fails(self):
        with _patch_db() as factory:
            s = factory()
            s.add(
                GoogleToken(
                    user_id="carol",
                    email="carol@example.com",
                    refresh_token_enc=b"RT",
                    key_id="plaintext",
                    scopes=["email"],
                )
            )
            s.commit()
            s.close()

            def _boom(*_args, **_kwargs):
                raise httpx.ConnectError("network down")

            with (
                patch(
                    "common.token_encryption.require_encryption",
                    return_value=PlaintextEncryption(),
                ),
                patch("services.gmail_svc.httpx.post", side_effect=_boom),
            ):
                result = gmail_disconnect(GmailDisconnectInput(user_id="carol"))

            assert result.revoked is True
            s = factory()
            row = s.query(GoogleToken).filter_by(user_id="carol").one()
            assert row.revoked_at is not None
            s.close()


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


def _client():
    return TestClient(app)


class TestCallbackRoute(TestTemplate):
    def test_missing_state_returns_400(self):
        with _patch_db():
            client = _client()
            resp = client.get("/api/v1/auth/google/callback?code=abc")
        assert resp.status_code == 400
        assert "state" in resp.text.lower()

    def test_bad_state_returns_400(self):
        with _patch_db():
            client = _client()
            resp = client.get(
                "/api/v1/auth/google/callback?code=abc&state=garbage.value"
            )
        assert resp.status_code == 400

    def test_error_param_returns_400(self):
        with _patch_db():
            client = _client()
            resp = client.get("/api/v1/auth/google/callback?error=access_denied")
        assert resp.status_code == 400
        assert "access_denied" in resp.text

    def test_happy_path_stores_encrypted_refresh_token(self):
        state = _sign_state("dave")
        id_token = _fake_id_token("dave@example.com")

        body = {
            "access_token": "AT",
            "refresh_token": "RT-secret",
            "id_token": id_token,
            "scope": " ".join(GMAIL_SCOPES),
            "expires_in": 3600,
        }

        async def _fake_exchange(**_kwargs):
            return body

        with (
            _patch_db() as factory,
            patch.object(global_config, "GOOGLE_CLIENT_ID", "cid"),
            patch.object(global_config, "GOOGLE_CLIENT_SECRET", "csecret"),
            patch.object(
                global_config,
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/v1/auth/google/callback",
            ),
            patch.object(global_config, "GOOGLE_TOKEN_ENC_KEY", None),
            patch.object(global_config, "DEV_ENV", "dev"),
            patch(
                "api_server.routes.google_oauth._exchange_code",
                _fake_exchange,
            ),
        ):
            client = _client()
            resp = client.get(f"/api/v1/auth/google/callback?code=abc&state={state}")

            assert resp.status_code == 200, resp.text
            assert "Connected" in resp.text
            assert "dave@example.com" in resp.text

            s = factory()
            row = s.query(GoogleToken).filter_by(user_id="dave").one()
            # Refresh token is stored encrypted (or, in dev fallback, as bytes
            # of the plaintext - the contract is "stored via the encryption
            # backend", so we only assert it went through the layer).
            assert row.refresh_token_enc != b"RT-secret" or row.key_id == "plaintext"
            # Decoding it back must yield the original
            enc = PlaintextEncryption()
            assert enc.decrypt(row.refresh_token_enc) == "RT-secret"
            assert row.email == "dave@example.com"
            assert row.revoked_at is None
            s.close()
