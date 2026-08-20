"""Scope + surface enforcement on the auto-registered ``/api/v1/services/*`` routes.

Administrative services (config / doctor) are **CLI-only**: they have no HTTP
route at all, so an authenticated caller gets a 404, not a 403. This pins the
CASA AL1 3.3.1 boundary the old admin-scope gate could not hold - every
interactive login carries ``scopes=["*"]``, so a wildcard identity satisfied
``admin:*`` and reached those endpoints. Removing the interface is the fix.

Ordinary services (including the non-administrative ``greet`` demo) keep a route
gated by the single ``services:execute`` scope.
"""

from contextlib import contextmanager
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.routes.services import _cli_only_services
from api_server.server import app
from db.base import Base
from db.engine import get_db_session
from services import discover_services, get_registry
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)

# The exact administrative services that must be off the HTTP surface entirely.
_CLI_ONLY = {"config_get", "config_set", "config_show", "doctor", "doctor_fix"}


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


_PREFIX = "/api/v1/services/"


def _service_routes() -> list[APIRoute]:
    """Every registered ``/api/v1/services/{name}`` route."""
    return [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(_PREFIX)
    ]


def _service_route_names() -> set[str]:
    """Names of every registered ``/api/v1/services/{name}`` route."""
    return {r.path[len(_PREFIX) :] for r in _service_routes()}


def _route_required_scopes(route: APIRoute) -> set[str] | None:
    """Scopes the route's ``require_scopes(...)`` dependency enforces.

    ``require_scopes(*scopes)`` returns a nested ``_check`` closure that captures
    the requested scopes as its single free variable; we read them back off the
    route's resolved dependency tree so the assertion measures the *actual* gate
    wired onto the endpoint, not a re-derivation of what it should be.

    Returns ``None`` (not an empty set) when no ``require_scopes`` dependency /
    scope tuple can be located. This reads private FastAPI + ``require_scopes``
    internals, so if either restructures (the dep is wrapped, the scopes stop
    living in a single closure cell), the caller can distinguish "introspection
    broke" from "route requires no scope" and fail with an honest message rather
    than a misleading wrong-scope one.
    """
    for dep in route.dependant.dependencies:
        call = dep.call
        if getattr(call, "__qualname__", "").startswith("require_scopes"):
            for cell in getattr(call, "__closure__", None) or ():
                value = cell.cell_contents
                if isinstance(value, tuple) and all(isinstance(s, str) for s in value):
                    return {s for s in value if isinstance(s, str)}
    return None


class TestServiceRouteSurface(TestTemplate):
    def test_cli_only_set_matches_expected(self):
        """The derived CLI-only set is exactly the five administrative services -
        ``greet`` is dropped so it stays REST-reachable."""
        assert _cli_only_services() == frozenset(_CLI_ONLY)

    def test_cli_only_services_have_no_route(self):
        """None of the administrative services register an HTTP route at all."""
        routes = _service_route_names()
        for name in _CLI_ONLY:
            assert name not in routes, f"{name} must not have an HTTP route"

    def test_registered_routes_cover_registry_minus_cli_only(self):
        """The HTTP surface is exactly the registry minus the CLI-only services -
        no CLI-only name leaks back on, and ``greet`` stays exposed."""
        discover_services()
        registered = {e.name for e in get_registry()}
        routes = _service_route_names()
        assert routes == registered - frozenset(_CLI_ONLY)
        assert "greet" in routes

    def test_every_registered_service_route_requires_only_execute(self):
        """Every registered route's actual scope gate is exactly
        ``{"services:execute"}`` - no admin scope survives on any endpoint. Reads
        the requirement off each route's resolved dependency, so a route wired
        with a different (or missing) scope would fail here.

        Why introspection rather than a behavioral sweep: proving a route
        requires *exactly* ``services:execute`` needs both a rejection (a
        ``services:read`` key 403s) *and* an acceptance (an ``services:execute``
        key succeeds). The acceptance leg would have to actually execute every
        service - real Gmail/PDF/DB backends - so it can't run surface-wide.
        Behavioral proof that the gate is enforced is spot-checked on ``greet``
        in ``TestServiceRouteScopes`` below; this test carries the surface-wide
        exact-scope guarantee.
        """
        routes = _service_routes()
        assert routes, "expected service routes to be registered"
        for route in routes:
            found = _route_required_scopes(route)
            assert found is not None, (
                f"{route.path}: could not locate a require_scopes dependency - "
                "the scope-introspection helper is likely stale, not the route"
            )
            assert found == {"services:execute"}, (
                f"{route.path} must require exactly services:execute, got {found}"
            )


class TestServiceRouteScopes(TestTemplate):
    def setup_method(self):
        # The success paths flow through ensure_daily_limit, which needs a DB
        # session; the reject paths short-circuit before it. Override both the
        # dependency and the direct use_db_session call so a status assertion
        # measures the surface/scope gate, not a missing DB.
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

    def test_wildcard_identity_gets_404_on_admin_services(self):
        """The exact case the old admin-scope gate failed to stop: a wildcard
        (``*``) identity - what every interactive login carries - must be unable
        to reach any administrative service. With no route registered, it 404s
        instead of executing.
        """
        with self._scoped_client(["*"]) as client:
            assert (
                client.post(
                    "/api/v1/services/config_set",
                    json={"key": "llm_config.cache_enabled", "value": "false"},
                    headers={"Idempotency-Key": "cfg-set-1"},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    "/api/v1/services/config_get", json={"key": "llm_config"}
                ).status_code
                == 404
            )
            assert (
                client.post("/api/v1/services/config_show", json={}).status_code == 404
            )
            assert client.post("/api/v1/services/doctor", json={}).status_code == 404
            assert (
                client.post("/api/v1/services/doctor_fix", json={}).status_code == 404
            )
            # The SSE variant that also ran doctor/doctor_fix over HTTP is gone
            # too - this was the largest removal, so it gets an explicit 404.
            assert (
                client.post("/api/v1/stream/doctor", json={"fix": True}).status_code
                == 404
            )

    def test_execute_scope_reaches_greet(self):
        """The non-administrative ``greet`` demo stays REST-reachable with the
        ordinary ``services:execute`` scope."""
        with self._scoped_client(["services:execute"]) as client:
            resp = client.post("/api/v1/services/greet", json={"name": "World"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello, World!"

    def test_read_only_key_rejected_from_greet(self):
        """A key lacking ``services:execute`` is still refused (403) from a
        registered service - the ordinary scope gate is intact."""
        with self._scoped_client(["services:read"]) as client:
            resp = client.post("/api/v1/services/greet", json={"name": "World"})
        assert resp.status_code == 403
