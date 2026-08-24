"""Tests for the public waitlist signup endpoint and service.

Uses an in-memory SQLite engine and patches ``services.waitlist_svc.use_db_session``
so the route exercises real insert/dedupe logic without a live Postgres. Resend
stays inert because no ``RESEND_API_KEY`` is configured under test, so the
best-effort background tasks early-return with no network call.
"""

from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.server import app
from db.base import Base
from db.models.waitlist_signups import WaitlistSignup
from services.waitlist_svc import _ok, _slack_escape
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)


@contextmanager
def _override_use_db_session():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _count() -> int:
    with _SessionLocal() as s:
        return s.execute(select(func.count()).select_from(WaitlistSignup)).scalar_one()


class TestWaitlist(TestTemplate):
    def setup_method(self):
        self._patchers = [
            patch("services.waitlist_svc.use_db_session", _override_use_db_session),
            # Hermetic: never let the best-effort Resend side effects make real
            # network calls, even if a dev/CI runner has RESEND keys in .env.
            patch("services.waitlist_svc.sync_to_resend"),
            patch("services.waitlist_svc.notify_new_signup"),
            patch("services.waitlist_svc.send_confirmation"),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app)
        # Start each test from an empty table.
        with _SessionLocal() as s:
            s.query(WaitlistSignup).delete()
            s.commit()

    def teardown_method(self):
        for p in self._patchers:
            p.stop()

    def test_valid_signup_stores_one_row(self):
        resp = self.client.post("/waitlist/join", json={"email": "a@b.com"})
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert _count() == 1

    def test_email_is_normalized(self):
        self.client.post("/waitlist/join", json={"email": "  A@B.COM "})
        with _SessionLocal() as s:
            row = s.query(WaitlistSignup).one()
        assert row.email == "a@b.com"
        assert row.source == "waitlist-page"

    def test_duplicate_is_idempotent(self):
        self.client.post("/waitlist/join", json={"email": "dup@b.com"})
        resp = self.client.post("/waitlist/join", json={"email": "dup@b.com"})
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert _count() == 1  # no second row, no error

    def test_honeypot_filled_stores_nothing(self):
        resp = self.client.post(
            "/waitlist/join", json={"email": "bot@b.com", "company": "Acme Inc"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"success": True}  # bot cannot tell it was caught
        assert _count() == 0

    def test_long_honeypot_still_dropped_not_422(self):
        # A bot filling a long honeypot value must be dropped with success, not
        # rejected by validation (which would tip it off / break the silent drop).
        resp = self.client.post(
            "/waitlist/join", json={"email": "bot@b.com", "company": "x" * 5000}
        )
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert _count() == 0

    def test_oversized_body_rejected_413(self):
        # A body over the route's cap is aborted before parsing, so a giant
        # honeypot payload can't be pulled into memory.
        resp = self.client.post(
            "/waitlist/join", json={"email": "a@b.com", "company": "x" * 20000}
        )
        assert resp.status_code == 413
        assert _count() == 0

    def test_invalid_email_rejected(self):
        resp = self.client.post("/waitlist/join", json={"email": "not-an-email"})
        assert resp.status_code == 422
        assert _count() == 0


class TestWaitlistHelpers:
    def test_slack_escape_neutralizes_mentions(self):
        # A crafted signup must not become a Slack broadcast mention.
        assert _slack_escape("<!channel>@e.com") == "&lt;!channel&gt;@e.com"
        assert _slack_escape("a&b") == "a&amp;b"

    def test_ok_is_2xx_only(self):
        assert _ok(200) and _ok(299)
        assert not _ok(302) and not _ok(404) and not _ok(500)
