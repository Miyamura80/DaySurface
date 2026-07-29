"""Root landing document, plus the fallback for paths nothing else claims.

The bare host - ``https://mcp.<domain>/`` - used to answer Starlette's
plain-text ``Not Found``, which tells an agent (or a human) nothing about the
MCP endpoint the host exists to serve. Both handlers here answer the same
question - *what is this server and how do I talk to it?* - one at ``/`` with a
200, one at every unmatched path with a 404 that names the endpoint the caller
was probably reaching for.

Routing note: ``mcp_server.server.mount_on`` mounts FastMCP's Starlette app at
``/`` (FastMCP serves ``/mcp`` internally), so *that* sub-app - not FastAPI -
handles every path the API's own routers don't claim. :func:`not_found_handler`
is therefore registered on the mounted sub-app in ``api_server/server.py``; a
FastAPI-level 404 handler would never fire.

Responses are content-negotiated: browsers (``Accept: text/html``) get a small
self-contained page, everything else - agents, ``curl``, registry crawlers -
gets JSON.
"""

import html
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.responses import Response

from api_server.auth.authkit_auth import authkit_domain
from common import global_config

router = APIRouter(tags=["index"])

MCP_PATH = "/mcp"

_LANDING_TEMPLATE = Path(__file__).with_name("_landing.html")

# Everything a caller can usefully reach, keyed by the name used in the JSON
# `endpoints` object. Kept honest by ``test_every_advertised_endpoint_resolves``,
# which walks this map against the live app - an entry that 404s is worse than
# no entry at all.
_ENDPOINTS: dict[str, str] = {
    "mcp": MCP_PATH,
    "health": "/health",
    "openapi": "/openapi.json",
    "api_docs": "/docs",
    "server_card": "/.well-known/mcp/server-card.json",
    "agent_card": "/.well-known/agent-card.json",
    "api_catalog": "/.well-known/api-catalog",
}

# Served only when OAuth is configured; the route 404s otherwise, so
# advertising it unconditionally would contradict `mcp.authentication`.
_OAUTH_ENDPOINTS: dict[str, str] = {
    "oauth_protected_resource": "/.well-known/oauth-protected-resource/mcp",
    "oauth_authorization_server": "/.well-known/oauth-authorization-server/mcp",
}

# Paths MCP clients, registries, and agents guess at, mapped to what this
# server actually serves. Legacy HTTP+SSE transport paths (/sse, /messages)
# dominate: clients written against the pre-streamable-HTTP spec probe them
# first. Matched after normalization (lowercased, trailing slash stripped).
#
# Nothing under /mcp belongs here: MCPAuthMiddleware sits outside this mount
# and answers every path starting with /mcp itself, so those rows would be
# unreachable - and :func:`not_found_handler` leaves that surface alone.
_ALIASES: dict[str, str] = {
    "/sse": MCP_PATH,
    "/messages": MCP_PATH,
    "/message": MCP_PATH,
    "/stream": MCP_PATH,
    "/api/mcp": MCP_PATH,
    "/v1/mcp": MCP_PATH,
    "/rpc": MCP_PATH,
    "/.well-known/mcp": "/.well-known/mcp/server-card.json",
    "/.well-known/mcp.json": "/.well-known/mcp/server-card.json",
    "/.well-known/agent.json": "/.well-known/agent-card.json",
    "/.well-known/ai-plugin.json": "/.well-known/mcp/server-card.json",
    "/openapi": "/openapi.json",
    "/swagger": "/docs",
    "/redoc": "/docs",
}


def _endpoints() -> dict[str, str]:
    """Paths to advertise, given what this deployment actually serves."""
    if authkit_domain():
        return {**_ENDPOINTS, **_OAUTH_ENDPOINTS}
    return dict(_ENDPOINTS)


def _base_url(request: Request) -> str:
    """Public origin to advertise in absolute URLs.

    Prefers the configured public hosts so the document matches what OAuth
    binds tokens to; falls back to the request's own origin (local dev, where
    neither is set and ``mcp_resource_url()`` would say ``localhost``).
    """
    if global_config.MCP_PUBLIC_URL:
        parts = urlsplit(global_config.MCP_PUBLIC_URL)
        return f"{parts.scheme}://{parts.netloc}"
    if global_config.API_PUBLIC_URL:
        return global_config.API_PUBLIC_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def _mcp_url(request: Request) -> str:
    """Absolute URL of the streamable-HTTP MCP endpoint."""
    if global_config.MCP_PUBLIC_URL:
        return global_config.MCP_PUBLIC_URL.rstrip("/")
    return f"{_base_url(request)}{MCP_PATH}"


def _authentication() -> dict:
    """How a client authenticates against /mcp.

    Every /mcp request is authenticated (see ``MCPAuthMiddleware``): an OAuth
    2.1 access token when AuthKit is configured, or an ``X-Api-Key`` header.
    """
    domain = authkit_domain()
    schemes = ["oauth2"] if domain else []
    schemes.append("api_key")
    auth: dict = {"required": True, "schemes": schemes}
    if domain:
        auth["authorization_servers"] = [domain]
        auth["protected_resource_metadata"] = _OAUTH_ENDPOINTS[
            "oauth_protected_resource"
        ]
    auth["api_key_header"] = "X-Api-Key"
    return auth


def _tool_names() -> list[str]:
    """Names of the tools exposed over MCP, from the live service registry.

    Imported lazily - ``mcp_server.server`` builds the FastMCP singleton at
    import time, so we defer it to request time to keep this module cheap to
    import. Names only: the server card carries the full descriptions.
    """
    from mcp_server.server import llm_tool_surface  # noqa: PLC0415

    return [entry.name for entry in llm_tool_surface()]


def _client_name(title: str) -> str:
    """Local name to suggest when registering this server with an MCP client."""
    return title.lower().replace(" ", "-")


def index_document(request: Request) -> dict:
    """The machine-readable "what is this host" document served at ``/``."""
    b = global_config.branding
    base = _base_url(request)
    mcp_url = _mcp_url(request)
    client_name = _client_name(b.title)
    return {
        "name": b.name,
        "title": b.title,
        "description": b.description,
        "protocol": "mcp",
        "instructions": (
            f"This host serves the Model Context Protocol at {mcp_url} over the "
            "streamable-HTTP transport. Point an MCP client at that URL - e.g. "
            f"`claude mcp add --transport http {client_name} {mcp_url}` - and "
            "authenticate as described under `mcp.authentication`. Read "
            "`endpoints.server_card` for the full tool descriptions without "
            "opening a session."
        ),
        "mcp": {
            "url": mcp_url,
            "transport": "streamable-http",
            "authentication": _authentication(),
            "tools": _tool_names(),
        },
        "endpoints": {name: f"{base}{path}" for name, path in _endpoints().items()},
        "links": {
            "website": b.website_url,
            "repository": b.repository_url,
        },
    }


def _prefers_html(request: Request) -> bool:
    """True for browsers. Agents and ``curl`` send ``*/*`` or a JSON type."""
    return "text/html" in request.headers.get("accept", "")


def _landing_page(doc: dict) -> str:
    """Self-contained HTML mirror of the index document (no external assets).

    The markup lives in ``_landing.html`` next door rather than in a Python
    string: an f-string would need every CSS brace doubled, and one missed
    escape is a runtime error on a public, unauthenticated endpoint.
    """
    e = html.escape
    endpoint_rows = "\n".join(
        f'<li><a href="{e(url)}">{e(name.replace("_", " "))}</a>'
        f"<code>{e(urlsplit(url).path)}</code></li>"
        for name, url in doc["endpoints"].items()
    )
    tools = doc["mcp"]["tools"]
    add_command = (
        f"claude mcp add --transport http {_client_name(doc['title'])} "
        f"{doc['mcp']['url']}"
    )
    return Template(_LANDING_TEMPLATE.read_text(encoding="utf-8")).substitute(
        title=e(doc["title"]),
        description=e(doc["description"]),
        mcp_url=e(doc["mcp"]["url"]),
        add_command=e(add_command),
        endpoint_rows=endpoint_rows,
        tool_count=len(tools),
        tool_list=", ".join(e(name) for name in tools) or "none registered",
        website=e(doc["links"]["website"]),
    )


# HEAD is spelled out because FastAPI, unlike plain Starlette, does not imply it
# from GET - and uptime probes and link checkers reach for HEAD first.
@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index(request: Request) -> Response:
    """Describe this host to whoever - or whatever - lands on it."""
    doc = index_document(request)
    # `Vary: Accept` is not optional: this host sits behind a CDN, and without
    # it the first response cached for "/" is served to everyone - agents get
    # the browser's HTML, or browsers get the agent's JSON.
    # `Access-Control-Allow-Origin` matches the well-known documents this points
    # at: registry crawlers and browser-based agents read them cross-origin.
    headers = {"Vary": "Accept", "Access-Control-Allow-Origin": "*"}
    if _prefers_html(request):
        return HTMLResponse(_landing_page(doc), headers=headers)
    return JSONResponse(doc, headers=headers)


def _suggest(path: str) -> str | None:
    """The endpoint a caller probing ``path`` most likely wanted, if any.

    A table lookup, deliberately: agents don't typo, they guess *structurally*
    (a transport path from an older spec, a registry's favourite filename), and
    fuzzy matching answers those no better while confidently mis-routing
    near-misses it has no business answering at all.
    """
    normalized = path.rstrip("/").lower() or "/"
    return _ALIASES.get(normalized)


def _is_mcp_path(path: str) -> bool:
    """True for the MCP transport surface: ``/mcp`` and anything beneath it."""
    return path == MCP_PATH or path.startswith(f"{MCP_PATH}/")


async def not_found_handler(request: Request, exc: Exception) -> Response:
    """404 that points the caller at the endpoints this host does serve.

    Registered on the mounted MCP sub-app (see the module docstring), which is
    what actually receives unmatched paths. The body uses the API's error
    envelope, so ``ErrorHandlerMiddleware`` passes it through and stamps the
    request ID on it.

    Registration is by status code, so this also sees any ``HTTPException(404)``
    raised *inside* the MCP transport. Today FastMCP emits its protocol 404s
    (terminated or unknown session) as direct responses, so none arrive here -
    but the transport owns that surface either way, and answering a stale
    session with a landing document would be a protocol error. Hence the guard:
    /mcp keeps the bare 404.
    """
    if _is_mcp_path(request.url.path):
        return PlainTextResponse("Not Found", status_code=404)

    base = _base_url(request)
    suggestion = _suggest(request.url.path)
    message = f"No handler for {request.method} {request.url.path}."
    if suggestion:
        message += f" Did you mean {base}{suggestion}?"
    else:
        message += f" This host serves the Model Context Protocol at {base}{MCP_PATH}."

    details: dict = {"path": request.url.path}
    if suggestion:
        details["did_you_mean"] = f"{base}{suggestion}"

    body = {
        "error": {"code": "not_found", "message": message, "details": details},
        "endpoints": {name: f"{base}{path}" for name, path in _endpoints().items()},
    }
    # Same CORS grant as the index: the browser-based agent that probed a wrong
    # path is exactly who the hint is for, and without this it reads an opaque
    # CORS failure instead.
    return JSONResponse(
        body, status_code=404, headers={"Access-Control-Allow-Origin": "*"}
    )
