"""FastAPI application - CORS, session middleware, route registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

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
from api_server.routes import (
    agentic_payments,
    ask,
    auth,
    google_oauth,
    health,
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
    _APP_VERSION = _pkg_version("mcp-template")
except PackageNotFoundError:
    _APP_VERSION = "0.1.0"

# Advertise the public host in the OpenAPI `servers` block when configured, so
# the published spec is self-describing (codegen, Swagger "Try it out", and the
# landing-page API reference all target the real deployment, not a relative path).
_openapi_servers = (
    [{"url": global_config.API_PUBLIC_URL}] if global_config.API_PUBLIC_URL else None
)

# Document the versioning + deprecation contract in the spec itself so codegen
# and agents discover it without reading prose docs. Endpoints are URL-versioned
# under `/api/v1`; deprecated operations additionally emit RFC 9745
# `Deprecation` and RFC 8594 `Sunset` response headers (see api_server/deprecation.py).
_API_DESCRIPTION = (
    "One codebase exposed over CLI, MCP, and HTTP.\n\n"
    "**Versioning:** endpoints are URL-versioned under `/api/v1`. Breaking "
    "changes ship under a new path prefix; the prior version keeps serving "
    "until its sunset date.\n\n"
    "**Deprecation policy:** a deprecated endpoint returns a `Deprecation` "
    "header (RFC 9745), a `Sunset` header (RFC 8594) once a removal date is "
    'set, and a `Link; rel="deprecation"` header pointing to the policy page. '
    "See https://gmailmcp.com/docs/api/deprecation.\n\n"
    "**Pagination:** list endpoints are cursor-based. Follow `next_cursor` "
    "until `has_more` is false."
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Compose the FastMCP session manager with the periodic webhook runner."""
    async with mcp_lifespan(app), runner_lifespan(app):
        yield


app = FastAPI(
    title="mymcp-api",
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
)

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=global_config.server.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Exception handlers ---------------------------------------------------
# Map the oversized-attachment domain error to 413 so an over-cap
# gmail_get_attachment request is a client error, not a generic 500.
app.add_exception_handler(GmailAttachmentTooLargeError, attachment_too_large_handler)

# --- Routes ---------------------------------------------------------------

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
mount_mcp_server(app)


def main() -> None:
    """Entry-point for ``mymcp-api`` console script."""
    uvicorn.run(
        "api_server.server:app",
        host=global_config.server.host,
        port=global_config.server.port,
        reload=False,
    )
