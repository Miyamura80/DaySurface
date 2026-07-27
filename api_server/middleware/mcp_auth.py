"""Authentication for the streamable-HTTP /mcp endpoint.

Accepts, in order: OAuth 2.1 access tokens issued by AuthKit (the MCP-spec
flow - discovery via RFC 9728, registration and PKCE handled by AuthKit),
WorkOS session JWTs, and API keys - so REST credentials keep working as a
parallel path. Implemented as pure ASGI middleware (not
:class:`BaseHTTPMiddleware`) so it doesn't buffer the streaming SSE responses
FastMCP emits.

401 responses advertise the Protected Resource Metadata URL via
``WWW-Authenticate`` when OAuth is configured, which is how MCP clients
bootstrap the authorization flow.
"""

import json

import anyio
from starlette.types import ASGIApp, Receive, Scope, Send

from api_server.auth.api_key_auth import validate_api_key
from api_server.auth.authkit_auth import (
    resource_metadata_url,
    verify_authkit_token,
)
from api_server.auth.unified_auth import AuthenticatedUser
from api_server.auth.workos_auth import verify_workos_token
from common import global_config
from db.engine import use_db_session
from src.utils.current_user import reset_current_user, set_current_user

_MCP_PATH_PREFIX = "/mcp"


class MCPAuthMiddleware:
    """Authenticate requests to /mcp and bind the user to a ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_MCP_PATH_PREFIX):
            await self.app(scope, receive, send)
            return

        user = await _authenticate_async(scope)
        if user is None:
            await _send_unauthorized(send)
            return

        token = set_current_user(user)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_user(token)


async def _authenticate_async(scope: Scope) -> AuthenticatedUser | None:
    """Run blocking auth I/O in a thread to avoid blocking the event loop."""
    return await anyio.to_thread.run_sync(lambda: _authenticate(scope))  # ty: ignore[unresolved-attribute]


def _authenticate(scope: Scope) -> AuthenticatedUser | None:
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
    }

    auth_header = headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        oauth_user = verify_authkit_token(token)
        if oauth_user:
            return AuthenticatedUser(
                user_id=oauth_user.user_id,
                email=oauth_user.email,
                auth_method="oauth",
                scopes=oauth_user.scopes,
            )
        workos_user = verify_workos_token(token)
        if workos_user:
            return AuthenticatedUser(
                user_id=workos_user.user_id,
                email=workos_user.email,
                auth_method="jwt",
                scopes=["*"],
            )
        if global_config.WORKOS_CLIENT_ID or global_config.WORKOS_AUTHKIT_DOMAIN:
            return None

    api_key = headers.get("x-api-key", "")
    if api_key:
        with use_db_session() as session:
            row = validate_api_key(session, api_key)
            if row:
                return AuthenticatedUser(
                    user_id=row.user_id,
                    auth_method="api_key",
                    scopes=row.scopes,
                )

    return None


def _www_authenticate_value() -> bytes:
    """Build the 401 challenge; advertise PRM discovery when OAuth is on."""
    value = 'Bearer realm="mcp"'
    if global_config.WORKOS_AUTHKIT_DOMAIN:
        value += f', resource_metadata="{resource_metadata_url()}"'
    return value.encode("latin-1")


async def _send_unauthorized(send: Send) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Authentication required"},
            "id": None,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", _www_authenticate_value()),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
