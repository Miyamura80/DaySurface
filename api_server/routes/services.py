"""Auto-register every service as an authenticated ``POST /api/v1/services/{name}``.

Administrative services (config/doctor) are **CLI-only**: they are not registered
as HTTP routes at all, so no administrative function is reachable over the
network. This closes CASA AL1 finding 3.3.1 ("administrative interfaces shall use
appropriate MFA") by removing the interface rather than adding a second factor -
every interactive login already satisfies single-factor ``admin:*`` (interactive
users get ``scopes=["*"]``), so a scope gate could not hold the boundary.

The CLI-only set is derived from the MCP transport's
``EXCLUDED_DEFAULT_MCP_SERVICES`` (the same services hidden from the LLM tool
surface), minus ``greet`` - the one non-administrative member, a plain demo
endpoint kept reachable over REST with the ordinary ``services:execute`` scope.
Deriving from that single source of truth fails closed: any service added to
``EXCLUDED_DEFAULT_MCP_SERVICES`` is off *both* remote transports (MCP and HTTP)
until it is explicitly exempted the way ``greet`` is.
"""

from fastapi import APIRouter, Depends, Request

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import SERVICES_EXECUTE, require_scopes
from api_server.billing.limits import ensure_daily_limit
from api_server.billing.paywall import enforce_payment
from api_server.idempotency import execute_idempotent
from services import ServiceEntry, discover_services, get_registry

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _cli_only_services() -> frozenset[str]:
    """Services with no HTTP route at all - reachable only through the CLI.

    Source of truth is the MCP transport's ``EXCLUDED_DEFAULT_MCP_SERVICES`` set
    (config/doctor administration + the ``greet`` demo). We reuse that constant
    rather than re-listing the names so the two remote transports can't drift,
    and drop only ``greet`` - the one non-administrative member, which stays
    reachable over REST with the ordinary ``services:execute`` grant.
    """
    # Deferred like the other api_server -> mcp_server imports (well_known.py,
    # index.py): keeps module import order robust even though there is no cycle.
    from mcp_server.server import EXCLUDED_DEFAULT_MCP_SERVICES  # noqa: PLC0415

    return EXCLUDED_DEFAULT_MCP_SERVICES - {"greet"}


def _register_service_routes() -> None:
    """Discover all service modules and create one route per non-CLI-only service.

    Services in :func:`_cli_only_services` get no route registered - no handler,
    no 403 stub, just a 404 for any HTTP caller. Every registered service uses
    the same ``services:execute`` scope.
    """
    discover_services()
    cli_only = _cli_only_services()
    for entry in get_registry():
        if entry.name in cli_only:
            continue
        _make_route(entry)


def _make_route(entry: ServiceEntry) -> None:
    """Register ``POST /api/v1/services/{name}`` for one service.

    Read-only services run the compute directly. Mutating services run the same
    compute through ``execute_idempotent``, which enforces ``Idempotency-Key``
    and replays the stored response on retries. Every route requires
    ``services:execute``; administrative services have no route at all (see the
    module docstring).
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
        _user: AuthenticatedUser = Depends(require_scopes(SERVICES_EXECUTE)),
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
