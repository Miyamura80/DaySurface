"""Integration tests for API server route registration."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.server import app
from common import global_config, token_encryption
from common.token_encryption import FernetEncryption
from db import engine as db_engine
from db.base import Base
from db.engine import get_db_session
from db.models.thread_curation import ThreadCuration
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
    """Yield a session from the in-memory test engine for use_db_session."""
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestAPIServer(TestTemplate):
    def setup_method(self):
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        # Patch use_db_session so ensure_daily_limit uses the test DB
        self._use_db_patcher = patch(
            "api_server.billing.limits.use_db_session",
            _override_use_db_session,
        )
        self._use_db_patcher.start()
        self.client = TestClient(app)

    def teardown_method(self):
        self._use_db_patcher.stop()
        app.dependency_overrides.clear()

    def test_health_endpoint(self):
        # Public /health is a liveness signal only; the component breakdown
        # moved to the authenticated /health/detail (tests/test_health_enhanced.py).
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}

    def test_health_detail_endpoint(self):
        resp = self.client.get("/health/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert data["components"]["api"]["status"] == "ok"

    def test_greet_service(self):
        resp = self.client.post(
            "/api/v1/services/greet",
            json={"name": "World"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Hello, World!"

    def test_service_requires_auth(self):
        """Without auth override, endpoints should require credentials."""
        # Remove only the auth override; keep the DB override
        app.dependency_overrides.pop(get_authenticated_user, None)
        with patch("api_server.auth.workos_auth.global_config") as mock_config:
            mock_config.WORKOS_CLIENT_ID = None
            client = TestClient(app)
            resp = client.post(
                "/api/v1/services/greet",
                json={"name": "World"},
            )
            assert resp.status_code == 401

    def test_me_endpoint(self):
        resp = self.client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "test-user"
        assert data["auth_method"] == "jwt"

    def test_service_routes_registered(self):
        """Ordinary services get API routes; CLI-only admin services do not."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/services/greet" in routes
        # All five config/doctor admin services are CLI-only - no HTTP route.
        for name in ("config_get", "config_set", "config_show", "doctor", "doctor_fix"):
            assert f"/api/v1/services/{name}" not in routes
        # The SSE doctor stream (which also ran doctor/doctor_fix over HTTP) is
        # removed too - it was the real doctor endpoint, so pin its absence.
        assert "/api/v1/stream/doctor" not in routes

    def test_mutating_service_requires_idempotency_key(self):
        """gmail_send is mutating=True, so its route enforces Idempotency-Key."""
        resp = self.client.post(
            "/api/v1/services/gmail_send",
            json={"draft_id": "draft-123"},
        )
        assert resp.status_code == 422
        assert "Idempotency-Key" in resp.json()["error"]["message"]

    def test_oversized_attachment_returns_413(self):
        """An over-cap gmail_get_attachment surfaces as 413, not a generic 500."""
        mock_svc = MagicMock()
        mock_svc.users().messages().attachments().get().execute.return_value = {
            "data": "QUJD",  # "ABC"
            "size": 10_000,
        }
        with (
            patch(
                "services.gmail_messages_svc._get_gmail_client", return_value=mock_svc
            ),
            patch.object(global_config.gmail, "max_attachment_bytes", 5),
        ):
            resp = self.client.post(
                "/api/v1/services/gmail_get_attachment",
                json={"message_id": "m-1", "attachment_id": "att-1"},
            )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "payload_too_large"

    def test_billing_routes_registered(self):
        """Billing routes should be registered."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/billing/checkout/create" in routes
        assert "/api/v1/billing/usage/current" in routes
        assert "/api/v1/billing/subscription/status" in routes

    def test_403_on_insufficient_scopes(self):
        """A key with read-only scopes should be rejected from service execution."""
        # Override auth to return a user with limited scopes
        original = app.dependency_overrides.get(get_authenticated_user)
        try:
            app.dependency_overrides[get_authenticated_user] = lambda: (
                AuthenticatedUser(
                    user_id="scoped-user",
                    auth_method="api_key",
                    scopes=["services:read"],
                )
            )
            client = TestClient(app)
            resp = client.post("/api/v1/services/greet", json={"name": "World"})
            assert resp.status_code == 403
        finally:
            # Restore original override to prevent leaking into other tests
            if original is not None:
                app.dependency_overrides[get_authenticated_user] = original
            else:
                app.dependency_overrides.pop(get_authenticated_user, None)


class ServiceRouteIdempotencyBase(TestTemplate):
    """Shared harness proving a ``mutating=True`` service's auto-generated
    route enforces Idempotency-Key end-to-end against the real route factory +
    ``execute_idempotent`` (not a synthetic stand-in route).

    Subclasses set ``ROUTE`` and implement:

    - ``_payload()`` / ``_conflicting_payload()`` - request bodies (the second
      must hash differently for the conflict test);
    - ``_mutation_count()`` - how many times the underlying side effect has
      actually happened (DB rows written, Gmail API calls made, ...), so the
      generic tests can assert 0 mutations without a key and exactly 1 across
      an execute + replay pair;
    - ``_extra_setup()`` / ``_extra_patchers()`` / ``_extra_teardown()`` -
      service-specific mocks and overrides.

    The base provides the fresh in-memory DB, auth override, idempotency +
    billing session patches, and the missing-key / replay / conflict triad.
    """

    ROUTE: str

    def setup_method(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_authenticated_user] = _override_auth
        self._extra_setup()

        self._patchers = [
            patch("api_server.idempotency.use_db_session", _ctx),
            patch("api_server.billing.limits.use_db_session", _ctx),
            # Disable opportunistic idempotency-key cleanup so it can't race.
            patch("api_server.idempotency.random.random", return_value=1.0),
            *self._extra_patchers(),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app)

    def teardown_method(self):
        for p in self._patchers:
            p.stop()
        self._extra_teardown()
        app.dependency_overrides.clear()

    # -- subclass hooks ----------------------------------------------------

    def _extra_setup(self) -> None:
        """Runs after the engine/auth override exist, before patchers start."""

    def _extra_patchers(self) -> list:
        return []

    def _extra_teardown(self) -> None:
        """Runs after patchers stop, before dependency overrides clear."""

    def _payload(self) -> dict:
        raise NotImplementedError

    def _conflicting_payload(self) -> dict:
        raise NotImplementedError

    def _mutation_count(self) -> int:
        raise NotImplementedError

    def _assert_first_response(self, resp) -> None:
        """Optional service-specific assertions on the first 200 response."""

    # -- generic Idempotency-Key triad -------------------------------------

    def test_missing_idempotency_key_422(self):
        """Without a key the route refuses - and the side effect never runs."""
        resp = self.client.post(self.ROUTE, json=self._payload())
        assert resp.status_code == 422
        assert "Idempotency-Key" in resp.json()["error"]["message"]
        assert self._mutation_count() == 0

    def test_replay_same_key_does_not_remutate(self):
        headers = {"Idempotency-Key": "k-replay"}
        first = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert first.status_code == 200, first.text
        self._assert_first_response(first)
        assert self._mutation_count() == 1
        # Retry with the same key: cached body replayed, no second side effect.
        second = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert second.status_code == 200
        assert second.json() == first.json()
        assert self._mutation_count() == 1

    def test_same_key_different_payload_conflicts(self):
        headers = {"Idempotency-Key": "k-conflict"}
        first = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert first.status_code == 200, first.text
        conflict = self.client.post(
            self.ROUTE, json=self._conflicting_payload(), headers=headers
        )
        assert conflict.status_code == 422


class TestInboxSaveCurationIdempotency(ServiceRouteIdempotencyBase):
    """inbox_save_curation: the mutation is a curation-ledger row write."""

    ROUTE = "/api/v1/services/inbox_save_curation"

    def _extra_setup(self):
        def _db_dep():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db_session] = _db_dep

        # Route every db.engine.use_db_session caller (idempotency claim/replay,
        # billing quota, and the curation ledger) at this fresh test DB. Both
        # globals must be set: _init_engine() only short-circuits when _engine
        # is non-None, otherwise it rebuilds from BACKEND_DB_URI and clobbers
        # _SessionLocal.
        self._orig_engine = db_engine._engine
        self._orig_session_local = db_engine._SessionLocal
        db_engine._engine = self.engine
        db_engine._SessionLocal = self.SessionLocal

    def _extra_patchers(self):
        return [
            # inbox_save_curation reads each thread's current historyId to stamp
            # the row; mock the Gmail client + batch fetch (no network).
            patch("services.inbox_curation_svc._get_gmail_client"),
            patch(
                "services.inbox_curation_svc._batch_get_threads",
                return_value={"t1": {"id": "t1", "historyId": "100"}},
            ),
            # Real encryption backend for summary/reasoning at rest.
            patch.object(
                token_encryption,
                "require_encryption",
                return_value=FernetEncryption(Fernet.generate_key().decode()),
            ),
        ]

    def _extra_teardown(self):
        db_engine._engine = self._orig_engine
        db_engine._SessionLocal = self._orig_session_local

    def _judgments(self, importance: float) -> dict:
        return {
            "judgments": [
                {
                    "thread_id": "t1",
                    "bucket": "needs_reply",
                    "importance": importance,
                    "summary": "deck due",
                }
            ]
        }

    def _payload(self) -> dict:
        return self._judgments(0.9)

    def _conflicting_payload(self) -> dict:
        return self._judgments(0.1)

    def _mutation_count(self) -> int:
        with self.SessionLocal() as s:
            return (
                s.query(ThreadCuration).filter(ThreadCuration.thread_id == "t1").count()
            )

    def _assert_first_response(self, resp):
        assert resp.json()["saved"] == 1


class TestGmailComposeIdempotency(ServiceRouteIdempotencyBase):
    """gmail_compose: the mutation is Gmail ``drafts().create()`` - a retried
    request with the same key must replay instead of creating a second draft
    (the P0 duplicate-draft bug).
    """

    ROUTE = "/api/v1/services/gmail_compose"

    def _extra_setup(self):
        # Mocked Gmail client: drafts().create() is the mutation under test.
        self.mock_svc = MagicMock()
        self.mock_svc.users().drafts().create().execute.return_value = {"id": "d-new"}
        # compose re-fetches the saved draft at format=full after create.
        self.mock_svc.users().drafts().get().execute.return_value = {
            "id": "d-new",
            "message": {
                "id": "m-new",
                "threadId": "t-new",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "To", "value": "alice@example.com"},
                        {"name": "Subject", "value": "Subj"},
                    ],
                    "body": {"data": "Qm9keSE"},  # "Body!"
                },
            },
        }
        # Reset call counts accrued while configuring return values above.
        self._create_execute = self.mock_svc.users().drafts().create().execute
        self._create_execute.reset_mock()

    def _extra_patchers(self):
        return [
            patch(
                "services.gmail_drafts_svc._get_gmail_client",
                return_value=self.mock_svc,
            ),
        ]

    def _payload(self) -> dict:
        return {"to": "alice@example.com", "subject": "Subj", "body": "Body!"}

    def _conflicting_payload(self) -> dict:
        return {**self._payload(), "subject": "Other"}

    def _mutation_count(self) -> int:
        return self._create_execute.call_count

    def _assert_first_response(self, resp):
        assert resp.json()["draft_id"] == "d-new"
