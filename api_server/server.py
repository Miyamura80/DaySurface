"""FastAPI application - CORS, session middleware, route registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Literal, TypedDict

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api_server.middleware.error_handler import (
    ErrorHandlerMiddleware,
    RequestIdMiddleware,
    attachment_too_large_handler,
)
from api_server.middleware.mcp_auth import MCPAuthMiddleware
from api_server.middleware.rate_limit import RateLimitMiddleware
from api_server.middleware.security_headers import SecurityHeadersMiddleware
from api_server.routes import (
    agentic_payments,
    ask,
    auth,
    google_oauth,
    health,
    index,
    services,
    stream,
    well_known,
)
from api_server.routes.google import webhooks as google_webhooks
from api_server.routes.payments import checkout, metering, subscription, webhooks
from api_server.runner import runner_lifespan
from common import global_config
from mcp_server.server import lifespan as mcp_lifespan
from mcp_server.server import mount_on as mount_mcp_server
from services.gmail_svc import GmailAttachmentTooLargeError

try:
    _APP_VERSION = _pkg_version("daysurface")
except PackageNotFoundError:
    _APP_VERSION = "0.1.0"

# Advertise the public host in the OpenAPI `servers` block when configured, so
# the published spec is self-describing (codegen, Swagger "Try it out", and the
# landing-page API reference all target the real deployment, not a relative path).
_openapi_servers = (
    [{"url": global_config.API_PUBLIC_URL}] if global_config.API_PUBLIC_URL else None
)

# Document the versioning contract in the spec itself so codegen and agents
# discover it without reading prose docs. Endpoints are URL-versioned under
# `/api/v1`.
_API_DESCRIPTION = (
    "One codebase exposed over CLI, MCP, and HTTP.\n\n"
    "**Versioning:** endpoints are URL-versioned under `/api/v1`. Breaking "
    "changes ship under a new path prefix; the prior version keeps serving "
    "until its sunset date.\n\n"
    "**Pagination:** list endpoints are cursor-based. Follow `next_cursor` "
    "until `has_more` is false."
)


class SessionCookiePolicy(TypedDict):
    """The subset of ``SessionMiddleware`` options this app pins deliberately."""

    https_only: bool
    same_site: Literal["lax", "strict", "none"]


def session_cookie_policy() -> SessionCookiePolicy:
    """Cookie flags for ``SessionMiddleware`` (ASVS V3.4.1 / V3.4.3).

    Starlette defaults ``https_only`` to False, which ships the session cookie
    without ``Secure`` - it would then travel over a downgraded http:// request.
    Turned on everywhere except local development, where the server is served
    over plain http and a ``Secure`` cookie would simply never be stored,
    breaking any browser flow that round-trips through the session.

    ``same_site`` is stated rather than inherited so the CSRF posture is
    explicit: ``lax`` still lets the cookie ride the top-level GET redirect
    back from Google's OAuth consent screen, which ``strict`` would drop.

    ``global_config.is_dev`` fails secure - an unset or misspelled ``DEV_ENV``
    reads as production - so a typo cannot drop the ``Secure`` flag.
    """
    return {"https_only": not global_config.is_dev, "same_site": "lax"}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Compose the FastMCP session manager with the periodic webhook runner."""
    async with mcp_lifespan(app), runner_lifespan(app):
        yield


app = FastAPI(
    title="daysurface-api",
    version=_APP_VERSION,
    description=_API_DESCRIPTION,
    servers=_openapi_servers,
    lifespan=_lifespan,
)

# --- Middleware (last-added = outermost in Starlette) ---------------------

app.add_middleware(ErrorHandlerMiddleware)  # type: ignore[arg-type]
app.add_middleware(RateLimitMiddleware)  # type: ignore[arg-type]
app.add_middleware(RequestIdMiddleware)  # type: ignore[arg-type]

# Pure ASGI middleware that only acts on /mcp; sits outside RequestId/RateLimit
# so authenticated SSE streams are not buffered through BaseHTTPMiddleware.
app.add_middleware(MCPAuthMiddleware)  # type: ignore[arg-type]

app.add_middleware(
    SessionMiddleware,  # type: ignore[arg-type]
    secret_key=global_config.SESSION_SECRET_KEY,
    **session_cookie_policy(),
)

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=global_config.server.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Outermost, deliberately: it is the only middleware that must stamp *every*
# byte-producing path, including CORS preflights, the MCPAuthMiddleware 401
# challenge (which writes to `send` directly), and the FastMCP mount below.
#
# On the mount: `mount_mcp_server(app)` calls `app.mount("/", mcp_app)`, which
# registers an ordinary route on `app.router`. `add_middleware` wraps the
# router, not the individual routes, so /mcp responses traverse this middleware
# exactly like /health does - the mount does not bypass it. That holds only
# because the mount goes through `app.mount`; a second ASGI app composed
# *around* `app` (e.g. `Mount("/mcp", mcp_app)` in a parent Starlette router)
# would need its own registration.
#
# The docs paths are read off the app rather than hardcoded in the middleware,
# so customising or disabling `docs_url`/`redoc_url` can never leave the looser
# CSP pointed at a path FastAPI no longer serves the bundles on.
app.add_middleware(
    SecurityHeadersMiddleware,  # type: ignore[arg-type]
    docs_paths=frozenset(
        path
        for path in (
            app.docs_url,
            app.redoc_url,
            app.swagger_ui_oauth2_redirect_url,
        )
        if path
    ),
)

# --- Exception handlers ---------------------------------------------------
# Map the oversized-attachment domain error to 413 so an over-cap
# gmail_get_attachment request is a client error, not a generic 500.
app.add_exception_handler(GmailAttachmentTooLargeError, attachment_too_large_handler)

# --- Routes ---------------------------------------------------------------

app.include_router(index.router)
app.include_router(health.router)
app.include_router(well_known.router)
app.include_router(services.router)
app.include_router(stream.router)
app.include_router(auth.router)
app.include_router(google_oauth.router)
app.include_router(checkout.router)
app.include_router(metering.router)
app.include_router(subscription.router)
app.include_router(webhooks.router)
app.include_router(google_webhooks.router)
app.include_router(agentic_payments.router)
app.include_router(ask.router)

# --- MCP server (streamable HTTP) -----------------------------------------
# Mounts FastMCP at /mcp so CLI/API/MCP share one process, port, and middleware.
_mcp_app = mount_mcp_server(app)

# The mount sits at "/", so it - not FastAPI - answers every path the routers
# above don't claim. Registering the 404 handler there is what turns Starlette's
# bare "Not Found" into a document that names the MCP endpoint; on the FastAPI
# app it would never fire.
_mcp_app.add_exception_handler(404, index.not_found_handler)


def main() -> None:
    """Entry-point for ``daysurface-api`` console script."""
    uvicorn.run(
        "api_server.server:app",
        host=global_config.server.host,
        port=global_config.server.port,
        reload=False,
    )
