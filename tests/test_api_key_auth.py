"""Tests for API key create / validate / revoke / expire flow."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api_server.auth.api_key_auth import (
    create_api_key,
    revoke_api_key,
    validate_api_key,
)
from db.base import Base
from tests.test_template import TestTemplate


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestAPIKeyAuth(TestTemplate):
    def test_create_and_validate(self):
        session = _make_session()
        raw_key, row = create_api_key(session, user_id="user-1", name="test-key")
        assert raw_key.startswith("sk_")
        assert row.key_prefix == raw_key[:11]

        validated = validate_api_key(session, raw_key)
        assert validated is not None
        assert validated.user_id == "user-1"

    def test_create_with_scopes(self):
        session = _make_session()
        scopes = ["services:read", "services:execute"]
        raw_key, row = create_api_key(
            session, user_id="scoped-user", name="scoped-key", scopes=scopes
        )
        assert row.scopes == scopes

        validated = validate_api_key(session, raw_key)
        assert validated is not None
        assert validated.scopes == scopes

    def test_create_without_scopes_is_none(self):
        session = _make_session()
        raw_key, row = create_api_key(session, user_id="legacy-user")
        assert row.scopes is None

    def test_invalid_key_returns_none(self):
        session = _make_session()
        assert validate_api_key(session, "sk_bogus") is None

    def test_revoked_key_rejected(self):
        session = _make_session()
        raw_key, row = create_api_key(session, user_id="user-2")
        revoke_api_key(session, key_id=row.id, user_id="user-2")
        assert validate_api_key(session, raw_key) is None

    def test_expired_key_rejected(self):
        session = _make_session()
        raw_key, row = create_api_key(
            session,
            user_id="user-3",
            expires_in_days=0,
        )
        # Force expiry into the past
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()

        assert validate_api_key(session, raw_key) is None

    def test_revoke_wrong_user_returns_false(self):
        session = _make_session()
        _raw_key, row = create_api_key(session, user_id="user-4")
        assert revoke_api_key(session, key_id=row.id, user_id="wrong-user") is False
