"""Auto-register every service as an authenticated ``POST /api/v1/services/{name}``."""

from fastapi import APIRouter, Depends, Request

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import (
    ADMIN_READ,
    ADMIN_WRITE,
    SERVICES_EXECUTE,
    require_scopes,
)
from api_server.billing.limits import ensure_daily_limit
from api_server.idempotency import execute_idempotent
from services import ServiceEntry, discover_services, get_registry

router = APIRouter(prefix="/api/v1/services", tags=["services"])

# Admin/dev-only services that must NOT be reachable by ordinary third-party
# ``services:execute`` keys: they read the server's config, tamper with the
# process-wide override file, or shell out on the host. Gating them behind the
# admin scopes keeps first-party interactive identities working (JWT / AuthKit
# users carry ``["*"]``, which satisfies any scope) while blocking scoped
# integration keys. Mirrors mcp_server._EXCLUDED_DEFAULT_MCP_SERVICES, which
# hides the same tools from the LLM surface.
_ADMIN_SERVICE_SCOPES: dict[str, str] = {
    "config_show": ADMIN_READ,
    "config_get": ADMIN_READ,
    "doctor": ADMIN_READ,
    "config_set": ADMIN_WRITE,
    "doctor_fix": ADMIN_WRITE,
}


def _required_scope(entry: ServiceEntry) -> str:
    """Scope required to call *entry* over HTTP (admin for dev/introspection)."""
    return _ADMIN_SERVICE_SCOPES.get(entry.name, SERVICES_EXECUTE)


def _register_service_routes() -> None:
    """Discover all service modules and create one route per service."""
    discover_services()
    for entry in get_registry():
        _make_route(entry)


def _make_route(entry: ServiceEntry) -> None:
    """Register ``POST /api/v1/services/{name}`` for one service.

    Read-only services run the compute directly. Mutating services run the same
    compute through ``execute_idempotent``, which enforces ``Idempotency-Key``
    and replays the stored response on retries.
    """
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model
    required_scope = _required_scope(entry)

    @router.post(
        f"/{entry.name}",
        response_model=output_model,
        summary=entry.description,
        name=f"svc_{entry.name}",
    )
    def _handler(
        body: input_model,  # ty: ignore[invalid-type-form]
        request: Request,
        _user: AuthenticatedUser = Depends(require_scopes(required_scope)),
    ):
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            body = body.model_copy(update={"user_id": _user.user_id})

        def _compute():
            # Quota is checked inside the compute so idempotent replays don't
            # double-count usage; the first execution still enforces the limit.
            ensure_daily_limit(_user.user_id)
            return func(body)

        if not entry.mutating:
            return _compute()
        return execute_idempotent(
            request=request,
            user_id=_user.user_id,
            route=entry.name,
            request_payload=body.model_dump(mode="json"),
            compute=_compute,
        )


_register_service_routes()
