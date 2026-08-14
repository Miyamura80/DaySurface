"""Scope enforcement on the auto-registered ``/api/v1/services/*`` routes.

Administrative services (config / doctor) are REST-reachable but gated behind
``admin:*``; ordinary services keep ``services:execute``. This pins the ASVS
V4.1.3 privilege boundary: a standard-scoped key cannot reach a config
mutation that writes a process-global override, and ``admin:*`` is a real,
enforced scope rather than a defined-but-unused one.
"""

from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.auth.scopes import validate_scopes
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


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def _override_use_db_session():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestServiceRouteScopes(TestTemplate):
    def setup_method(self):
        # The success paths flow through ensure_daily_limit, which needs a DB
        # session; the reject paths short-circuit at the scope check before it.
        # Override both dependency and the direct use_db_session call so a
        # 403-vs-200 assertion measures the scope gate, not a missing DB.
        app.dependency_overrides[get_db_session] = _override_db
        self._use_db_patcher = patch(
            "api_server.billing.limits.use_db_session",
            _override_use_db_session,
        )
        self._use_db_patcher.start()

    def teardown_method(self):
        self._use_db_patcher.stop()
        app.dependency_overrides.clear()

    @contextmanager
    def _scoped_client(self, scopes: list[str]):
        """TestClient authenticating as an api_key user with exactly *scopes*."""
        app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
            user_id="scoped-user", auth_method="api_key", scopes=scopes
        )
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(get_authenticated_user, None)

    def test_config_set_rejects_standard_key(self):
        """Finding A: config_set writes a process-global config affecting all
        tenants. A standard-scoped key must be rejected (403), never reach it.
        """
        standard = validate_scopes(["standard"])
        # Guards Finding B: the standard template must not carry admin:write.
        assert "admin:write" not in standard
        with self._scoped_client(standard) as client:
            resp = client.post(
                "/api/v1/services/config_set",
                json={"key": "llm_config.cache_enabled", "value": "false"},
                headers={"Idempotency-Key": "cfg-set-1"},
            )
        assert resp.status_code == 403

    def test_config_read_requires_admin_read(self):
        """config_show / config_get stay REST-reachable but require admin:read,
        so a standard key is refused."""
        standard = validate_scopes(["standard"])
        assert "admin:read" not in standard
        with self._scoped_client(standard) as client:
            show = client.post("/api/v1/services/config_show", json={})
            get = client.post(
                "/api/v1/services/config_get",
                json={"key": "llm_config"},
            )
        assert show.status_code == 403
        assert get.status_code == 403

    def test_admin_key_can_read_config(self):
        """Finding B: admin:* is a real, enforced boundary - an admin key
        succeeds on config_show where a standard key is blocked."""
        with self._scoped_client(validate_scopes(["admin"])) as client:
            resp = client.post("/api/v1/services/config_show", json={})
        assert resp.status_code == 200
        assert "config" in resp.json()

    def test_execute_scope_still_reaches_nonadmin_service(self):
        """Ordinary services aren't over-gated: services:execute reaches greet,
        which is excluded from the MCP tool surface but not administrative."""
        with self._scoped_client(["services:execute"]) as client:
            resp = client.post("/api/v1/services/greet", json={"name": "World"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello, World!"
