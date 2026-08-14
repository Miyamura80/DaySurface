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
from api_server.billing.paywall import enforce_payment
from api_server.idempotency import execute_idempotent
from services import ServiceEntry, discover_services, get_registry

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _admin_only_services() -> frozenset[str]:
    """Services whose REST route requires an administrative scope.

    Source of truth is the MCP transport's ``EXCLUDED_DEFAULT_MCP_SERVICES`` set
    (config/doctor administration + the ``greet`` demo). We reuse that constant
    rather than re-listing the names so the two transports can't drift, and drop
    only ``greet`` - the one non-administrative member, a plain demo endpoint
    that stays reachable with the ordinary ``services:execute`` grant. Deriving
    this way fails closed: any service later hidden from the MCP tool surface is
    admin-gated here until it is explicitly exempted, never silently downgraded.
    """
    # Deferred like the other api_server -> mcp_server imports (well_known.py,
    # index.py): keeps module import order robust even though there is no cycle.
    from mcp_server.server import EXCLUDED_DEFAULT_MCP_SERVICES  # noqa: PLC0415

    return EXCLUDED_DEFAULT_MCP_SERVICES - {"greet"}


def _required_scope(entry: ServiceEntry, admin_services: frozenset[str]) -> str:
    """Scope required to invoke this service over REST.

    Administrative services (config/doctor) require an admin scope - ``admin:write``
    for the mutating ones (``config_set``, ``doctor_fix``), ``admin:read`` for the
    read-only ones. Every other service keeps the standard ``services:execute``
    grant. This is the only place ``admin:*`` is enforced, so a ``standard`` key
    (which lacks it) is rejected from these privileged functions with a 403.
    """
    if entry.name in admin_services:
        return ADMIN_WRITE if entry.mutating else ADMIN_READ
    return SERVICES_EXECUTE


def _register_service_routes() -> None:
    """Discover all service modules and create one route per service."""
    discover_services()
    admin_services = _admin_only_services()
    for entry in get_registry():
        _make_route(entry, _required_scope(entry, admin_services))


def _make_route(entry: ServiceEntry, required_scope: str) -> None:
    """Register ``POST /api/v1/services/{name}`` for one service.

    Read-only services run the compute directly. Mutating services run the same
    compute through ``execute_idempotent``, which enforces ``Idempotency-Key``
    and replays the stored response on retries. ``required_scope`` is the scope
    the caller must hold (``services:execute`` for ordinary services, ``admin:*``
    for administrative ones - see ``_required_scope``).
    """
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

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
            # Priced services are gated by the x402 paywall (verify + settle the
            # X-PAYMENT header). When the paywall actually charges the call it
            # bypasses the free quota; when it's inactive (x402 disabled) it
            # returns False and we still apply the daily quota, so a priced
            # service is never unlimited. Both run inside the compute so
            # idempotent replays don't double-charge or double-count.
            charged = False
            price = entry.price
            if price is not None:
                charged = enforce_payment(
                    user_id=_user.user_id,
                    route=entry.name,
                    price=price,
                    asset=entry.asset,
                    payment_header=request.headers.get("X-PAYMENT"),
                    mutating=entry.mutating,
                )
            if not charged:
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
