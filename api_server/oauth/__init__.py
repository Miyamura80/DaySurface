"""DaySurface's own OAuth 2.1 authorization server for MCP clients.

Enabled by ``MCP_AUTH_MODE=self``. In the default ``authkit`` mode nothing here
is mounted, and MCP clients keep getting tokens directly from WorkOS AuthKit.

    provider.py   token storage, rotation and revocation (MCP SDK provider)
    flow.py       consent page and WorkOS sign-in (browser leg)
    cimd.py       Client ID Metadata Document fetch and validation
    signing.py    signed, short-lived values carried through the browser
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.routes import build_metadata, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import global_config

_METADATA_PATH = "/.well-known/oauth-authorization-server"
_AUTHORIZE_PATH = "/authorize"
_REVOKE_PATH = "/revoke"
# Public clients only (see cimd.parse_document): no client authentication.
_CLIENT_AUTH_METHODS = ["none"]


def is_self_mode() -> bool:
    return global_config.MCP_AUTH_MODE == "self"


def _options() -> tuple[ClientRegistrationOptions, RevocationOptions]:
    # Dynamic Client Registration is deprecated in the MCP spec; clients use
    # Client ID Metadata Documents instead.
    return ClientRegistrationOptions(enabled=False), RevocationOptions(enabled=True)


def authorization_server_metadata() -> dict[str, Any]:
    """RFC 8414 metadata for DaySurface's authorization server."""
    from api_server.oauth.flow import issuer_url  # noqa: PLC0415

    issuer = issuer_url()
    registration, revocation = _options()
    metadata = build_metadata(AnyHttpUrl(issuer), None, registration, revocation)
    document = metadata.model_dump(mode="json", exclude_none=True)
    # Exact string, no trailing slash: clients compare the authorization
    # response's iss to this value byte for byte (RFC 9207).
    document["issuer"] = issuer
    document["code_challenge_methods_supported"] = ["S256"]
    document["token_endpoint_auth_methods_supported"] = _CLIENT_AUTH_METHODS
    document["revocation_endpoint_auth_methods_supported"] = _CLIENT_AUTH_METHODS
    document["client_id_metadata_document_supported"] = True
    document["authorization_response_iss_parameter_supported"] = True
    return document


def _require_deployment_settings() -> None:
    """Refuse to run the OAuth server outside development without its settings.

    * ``SESSION_SECRET_KEY`` signs the pending authorization and the WorkOS
      state; with the placeholder, anyone could forge them. The general startup
      check only fires for ``DEV_ENV=prod`` exactly, so this covers every
      non-development value.
    * ``MCP_PUBLIC_URL`` is the token audience and the issuer's origin; without
      it both fall back to localhost, which no remote client can reach.
    """
    if global_config.is_dev:
        return
    default = type(global_config).model_fields["SESSION_SECRET_KEY"].default
    if default == global_config.SESSION_SECRET_KEY:
        raise RuntimeError(
            "MCP_AUTH_MODE=self requires SESSION_SECRET_KEY to be set to a real secret."
        )
    if not global_config.MCP_PUBLIC_URL:
        raise RuntimeError("MCP_AUTH_MODE=self requires MCP_PUBLIC_URL to be set.")


class _IssuerOnRedirects:
    """Add ``iss`` to every redirect /authorize sends back to a client (RFC 9207).

    DaySurface's own redirects already carry it; this covers the error redirects
    the MCP SDK sends itself (an invalid scope, say). Only the redirect to the
    consent page is left alone; a client whose redirect URI happens to be on
    this origin still gets ``iss``.
    """

    def __init__(self, app: ASGIApp, issuer: str) -> None:
        self.app = app
        self.issuer = issuer
        self.consent_url = f"{issuer}/oauth/consent?"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_issuer(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in (
                302,
                303,
                307,
            ):
                headers = [
                    (
                        name,
                        self._with_issuer(value)
                        if name.lower() == b"location"
                        else value,
                    )
                    for name, value in message.get("headers", [])
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_issuer)

    def _with_issuer(self, raw: bytes) -> bytes:
        location = raw.decode("latin-1")
        if location.startswith(self.consent_url) or "iss" in parse_qs(
            urlsplit(location).query
        ):
            return raw
        return construct_redirect_uri(location, iss=self.issuer).encode("latin-1")


def create_sdk_routes() -> list[Route]:
    """/authorize and /token from the MCP SDK, plus DaySurface's /revoke.

    Changes from the SDK's set:

    * Metadata is served by ``api_server/routes/well_known.py`` from
      :func:`authorization_server_metadata`, which adds the CIMD and
      issuer-parameter fields the SDK does not emit.
    * /authorize is wrapped so the SDK's own error redirects carry ``iss``.
    * /revoke: the SDK's handler (mcp 1.30) requires a ``client_secret`` form
      field even for public clients, so a client that correctly omits it gets a
      400 and can never revoke. :func:`_revoke` follows RFC 7009 instead.
    """
    from api_server.oauth.flow import issuer_url  # noqa: PLC0415
    from api_server.oauth.provider import DaySurfaceOAuthProvider  # noqa: PLC0415

    _require_deployment_settings()
    issuer = issuer_url()
    provider = DaySurfaceOAuthProvider(issuer)
    registration, revocation = _options()
    routes: list[Route] = []
    for route in create_auth_routes(
        provider,
        AnyHttpUrl(issuer),
        client_registration_options=registration,
        revocation_options=revocation,
    ):
        path = getattr(route, "path", None)
        if path in (_METADATA_PATH, _REVOKE_PATH) or not isinstance(route, Route):
            continue
        if path == _AUTHORIZE_PATH:
            route = Route(
                _AUTHORIZE_PATH,
                endpoint=_IssuerOnRedirects(route.app, issuer),
                methods=["GET", "POST"],
            )
        routes.append(route)

    async def revoke(request: Request) -> Response:
        return await _revoke(provider, request)

    return [*routes, Route(_REVOKE_PATH, revoke, methods=["POST"])]


async def _revoke(provider: Any, request: Request) -> Response:
    """RFC 7009 token revocation for public clients.

    The client is identified by ``client_id``. A token is revoked only when it
    belongs to that client; an unknown or already-revoked token still gets 200,
    so the endpoint reveals nothing about which tokens exist.
    """
    form = await request.form()
    client_id = form.get("client_id")
    token = form.get("token")
    if not isinstance(client_id, str) or not isinstance(token, str) or not token:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    client = await provider.get_client(client_id)
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    found = await provider.load_access_token(token)
    if found is None:
        found = await provider.load_refresh_token(client, token)
    if found is not None and found.client_id == client.client_id:
        await provider.revoke_token(found)
    return Response(status_code=200)
