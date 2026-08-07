"""Tests for the health-check endpoints.

Public ``/health`` must stay a bare liveness signal (ASVS V14.3.3 - no build
identity or component breakdown for anonymous callers); the detailed payload
is only reachable through the authenticated ``/health/detail``.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.routes import health as health_module
from api_server.server import app
from db.base import Base
from db.engine import get_db_session
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)

# Keys that must never appear in the anonymous /health response.
_DISCLOSING_KEYS = ("version", "commit", "components", "timestamp")


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestHealthPublic(TestTemplate):
    """Anonymous callers get a liveness signal and nothing else."""

    def setup_method(self):
        app.dependency_overrides.clear()
        # Only the DB session is overridden (no BACKEND_DB_URI under test);
        # get_authenticated_user is deliberately left real so the client is
        # genuinely anonymous -- same pattern as tests/test_unified_auth.py.
        app.dependency_overrides[get_db_session] = _override_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_health_is_minimal(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"status"}
        assert data["status"] == "ok"

    def test_health_discloses_no_build_identity(self):
        data = self.client.get("/health").json()
        for key in _DISCLOSING_KEYS:
            assert key not in data, f"/health must not disclose {key!r}"

    def test_health_body_contains_no_commit_sha(self):
        # Belt-and-braces: the SHA must not leak through some other key either.
        with patch(
            "api_server.routes.health._get_git_commit", return_value="deadbee"
        ) as mock_commit:
            body = self.client.get("/health").text
        assert "deadbee" not in body
        mock_commit.assert_not_called()

    @patch("api_server.routes.health._check_database")
    def test_public_health_runs_no_dependency_probes(self, mock_db):
        """Liveness must not touch the database.

        The engine has `pool_pre_ping=True` and no connect timeout, so a probe
        against an unreachable host blocks on OS TCP retries. On this sync
        route that parks an anyio threadpool thread per caller, and the
        Dockerfile healthcheck (`--timeout=5s --retries=3`) would restart the
        container. Readiness lives on /health/detail instead.
        """
        mock_db.return_value = {"status": "error", "message": "OperationalError"}
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        mock_db.assert_not_called()
        assert "OperationalError" not in resp.text

    def test_health_detail_requires_auth(self):
        resp = self.client.get("/health/detail")
        assert resp.status_code == 401
        assert "commit" not in resp.text


class TestHealthDetailAuthenticated(TestTemplate):
    """Authenticated callers still get the full payload."""

    def setup_method(self):
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_detail_returns_components(self):
        resp = self.client.get("/health/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert "api" in data["components"]
        assert data["components"]["api"]["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data
        assert "commit" in data

    def test_detail_status_field(self):
        data = self.client.get("/health/detail").json()
        assert data["status"] in ("ok", "degraded")

    @patch("api_server.routes.health._check_database")
    def test_degraded_when_db_down(self, mock_db):
        mock_db.return_value = {"status": "error", "message": "connection refused"}
        data = self.client.get("/health/detail").json()
        assert data["status"] == "degraded"
        assert data["components"]["database"]["status"] == "error"

    def test_component_error_never_leaks_exception_message(self):
        # Unchanged pre-existing contract: a failing probe reports
        # type(exc).__name__ and never the exception text (which can carry a
        # DSN with credentials).
        with patch.object(
            health_module,
            "_get_redis_health_client",
            side_effect=RuntimeError("redis://user:pa55w0rd@host"),
        ):
            result = health_module._check_redis()
        assert result == {"status": "error", "message": "RuntimeError"}
