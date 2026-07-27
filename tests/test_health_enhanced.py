"""Tests for enhanced health check endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.routes.health import _health_cache
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


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestHealthEnhanced(TestTemplate):
    def setup_method(self):
        _health_cache.clear()
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_health_returns_components(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert "api" in data["components"]
        assert data["components"]["api"]["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data

    def test_health_status_field(self):
        resp = self.client.get("/health")
        data = resp.json()
        assert data["status"] in ("ok", "degraded")

    @patch("api_server.routes.health._check_database")
    def test_degraded_when_db_down(self, mock_db):
        mock_db.return_value = {"status": "error", "message": "connection refused"}
        resp = self.client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"]["status"] == "error"
