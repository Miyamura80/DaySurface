"""FastMCP server that registers services as MCP tools and exposes app HTML resources.

Two registration paths (see `_tool_factory.py` for details):

- **Headless** (default): sync wrapper, returns the Pydantic output model directly.
- **Enhanced** (opt-in via `@enhance`): async wrapper with `Context`, may elicit
  user input, attach images/audio, or render an MCP App (iframe dashboard).

Primary transport is streamable HTTP, mounted on the FastAPI app at ``/mcp``
(see ``api_server/server.py``). Stdio is supported via the ``daysurface-mcp``
console script for local dev / Claude Desktop only.

See ``mcp_server/MCP_UI_ARCHITECTURE.md`` and ``mcp_server/MCP_UI_EDGE_CASES.md``.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger as log
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from common import global_config
from mcp_server._tool_factory import make_tool
from services import ServiceEntry, discover_services, get_registry

_APPS_DIR = Path(__file__).parent / "apps"
_APP_MIME_TYPE = "text/html;profile=mcp-app"

# Module-level singleton: app_tools / enhancers may import this at module-load
# time (e.g. ``@mcp.tool``), so it must exist before discovery runs.
_MCP_INSTRUCTIONS = (
    "When the user asks to draft, edit, reply to, or compose an email, "
    "ALWAYS use the gmail_reply_to_thread, gmail_compose, or gmail_update_draft tools. "
    "NEVER write email draft text as plain chat text - the tools render an interactive "
    "composer UI where the user can review, edit, and send. "
    "Pass your composed text in the tool's 'body' parameter and keep your chat response "
    "to one brief sentence. "
    "For PDF attachments: fill forms with pdf_open + pdf_edit, then hand off with "
    "pdf_request_signature - signing is done by the USER typing their own name in "
    "the signing UI. Never type a signature value into a form field on the user's "
    "behalf, and never treat a document as signed until the user completes the "
    "ceremony. Deliver the result with pdf_export."
)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding allowlist for the streamable-HTTP transport.

    FastMCP enables DNS-rebinding protection and, when constructed with the
    default loopback host, only whitelists localhost - so any real deployment
    behind a custom domain gets ``421 Misdirected Request`` on ``/mcp``. We keep
    the protection on but additionally allow the public host derived from
    ``MCP_PUBLIC_URL`` (so it auto-tracks whatever domain the server is deployed
    at) plus loopback for stdio/local dev and the test client.
    """
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "localhost", "127.0.0.1"]
    # Loopback origins (any port) mirror the loopback hosts above: stdio/local
    # dev, the test client, and the conformance harness (which drives /mcp from
    # http://127.0.0.1:<port>) send an Origin header that must be allowed, or
    # the DNS-rebinding middleware answers 403. A remote attacker cannot forge a
    # loopback Origin, so this does not weaken cross-origin protection.
    loopback_origins = [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "http://localhost:*",
        "http://127.0.0.1:*",
        "http://[::1]:*",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost:*",
        "https://127.0.0.1:*",
        "https://[::1]:*",
    ]
    origins: list[str] = [*loopback_origins, *global_config.server.allowed_origins]
    public = global_config.MCP_PUBLIC_URL
    if public:
        parts = urlsplit(public)
        if parts.netloc:
            # Exact entry covers the default-port (443/80) case where the Host
            # header carries no port; the ":*" entry covers explicit ports.
            hosts.extend([parts.netloc, f"{parts.netloc}:*"])
            origins.append(f"{parts.scheme or 'https'}://{parts.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# ``stateless_http=True`` (the config default) makes every /mcp request
# self-contained: the server does not persist per-session state keyed by
# ``Mcp-Session-Id`` in memory. Stateful mode (the FastMCP default) breaks in
# exactly the deployments this template targets - Render free-tier spins down
# when idle, redeploys restart the process, and horizontal scaling routes
# requests across replicas - each of which wipes/splits the in-memory session
# store, so a client's next POST fails with "MCP session has been terminated or
# no longer exists on the server" (mcp_session_terminated). Some clients
# (OpenAI, and connectors that DELETE the session after each call) trigger the
# same error even single-replica. Stateless mode sidesteps all of it.
#
# The trade-off: EVERY server-initiated request hangs stateless - sampling
# (python-sdk issue 678) AND in-band elicitation. The elicitation/create
# request does reach the client on the tool call's SSE stream, but the
# client's response arrives as a new POST that lands on a fresh transient
# session and is dropped, so ``ctx.elicit()`` never resolves (verified
# empirically against this SDK). Rendering Apps and attaching content are
# unaffected. That is why every elicitation call site gates on the client
# having DECLARED the capability: under stateless HTTP ``client_params`` is
# never populated, the check correctly returns False, and the enhancers take
# their non-elicitation fallbacks. Deployments that want host-native
# elicitation (e.g. the PDF signing confirmation dialog) must run a single
# replica and set ``server.mcp_stateless_http: false``.
#
# Stateless is also where the spec is going: the MCP core is dropping session
# state at the protocol layer (SEP-2567 removes Mcp-Session-Id, SEP-2575
# removes the initialize handshake; 2026-07-28 RC), so "any request can land on
# any instance." stateless_http=True is the SDK option the transport roadmap
# points implementers to today. It still runs the initialize handshake (the SDK
# keeps that until it implements those SEPs) but stops persisting/validating
# per-session state, which is what actually fixes the error - and it's
# forward-compatible with the RC.
mcp: FastMCP = FastMCP(
    "daysurface",
    instructions=_MCP_INSTRUCTIONS,
    transport_security=_transport_security(),
    stateless_http=global_config.server.mcp_stateless_http,
)

_populated: bool = False
_EXCLUDED_DEFAULT_MCP_SERVICES = frozenset(
    {
        "config_get",
        "config_set",
        "config_show",
        "doctor",
        "doctor_fix",
        "greet",
    }
)


def build_mcp_server() -> FastMCP:
    """Populate the FastMCP singleton and return it. Idempotent."""
    global _populated
    if _populated:
        return mcp

    # Deferred by design (see CLAUDE.md): discovery imports app_tools /
    # enhancer modules at registration time, and those modules import this
    # module's `mcp` singleton back, so the packages must only load here.
    from mcp_server.app_tools import discover_app_tools  # noqa: PLC0415
    from mcp_server.enhancers import discover_enhancers  # noqa: PLC0415

    discover_services()
    discover_enhancers()
    discover_app_tools()

    for entry in get_registry():
        if entry.name in _EXCLUDED_DEFAULT_MCP_SERVICES:
            log.debug("Skipping default MCP registration for service {!r}", entry.name)
            continue
        make_tool(mcp, entry)
    _register_app_resources(mcp)

    _populated = True
    return mcp


def llm_tool_surface() -> list[ServiceEntry]:
    """Service entries this server exposes to the LLM as MCP tools.

    The registry minus the CLI-only defaults that ``build_mcp_server`` skips.
    App-only tools (registered directly via ``@mcp.tool`` and hidden from the
    LLM) are not in the service registry, so they are excluded automatically.

    This is the LLM-facing tool surface advertised pre-connection in the
    SEP-2127 server card (``/.well-known/mcp/server-card.json``). Sorted by name
    so the committed landing-page snapshot is stable across environments.
    """
    build_mcp_server()
    surface = (
        e for e in get_registry() if e.name not in _EXCLUDED_DEFAULT_MCP_SERVICES
    )
    return sorted(surface, key=lambda e: e.name)


def _register_app_resources(mcp: FastMCP) -> None:
    """Register ui:// resources for each MCP App with a built dist/mcp-app.html."""
    if not _APPS_DIR.is_dir():
        return
    for app_dir in sorted(_APPS_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        html_path = app_dir / "dist" / "mcp-app.html"
        uri = f"ui://daysurface/{app_dir.name}"
        _register_app_resource(mcp, uri, html_path, app_dir.name)


def _register_app_resource(
    mcp: FastMCP, uri: str, html_path: Path, app_name: str
) -> None:
    @mcp.resource(uri, mime_type=_APP_MIME_TYPE, name=f"{app_name} app")
    def _read_app() -> str:
        if not html_path.exists():
            log.warning("MCP App {!r} missing build at {}", app_name, html_path)
            return f"<!-- {app_name} not built. Run `make build_apps`. -->"
        return html_path.read_text()


def mount_on(app, path: str = "/mcp") -> Starlette:
    """Mount the streamable-HTTP MCP server onto a Starlette/FastAPI app.

    FastMCP's ``streamable_http_app()`` already serves at ``/mcp`` internally,
    so we mount it at root to avoid a doubled prefix. Caller must also include
    :func:`lifespan` in the parent app's lifespan to start the session manager.

    Returns the mounted sub-app. Because the mount sits at ``/``, it - not the
    parent - receives every path the parent's own routes don't claim, so a
    caller that wants to customize the 404 for unknown paths must register the
    handler on the returned app.
    """
    if path != "/mcp":
        raise ValueError(
            "Custom mount paths are not supported; FastMCP serves at /mcp internally."
        )
    mcp = build_mcp_server()
    mcp_app = mcp.streamable_http_app()
    app.mount("/", mcp_app)
    return mcp_app


@asynccontextmanager
async def lifespan(_app):
    """Async context manager that runs FastMCP's streamable-HTTP session manager.

    The parent FastAPI app must include this in its ``lifespan=`` argument or
    incoming /mcp requests will fail with "Task group is not initialized".
    """
    mcp = build_mcp_server()
    sm = mcp.session_manager
    # StreamableHTTPSessionManager.run() refuses re-entry once _has_started is set.
    # Reset it so the same instance can be restarted (tests with --count, hot-reload).
    sm._has_started = False
    async with sm.run():
        yield


def main() -> None:
    """Run the MCP server on stdio transport (legacy / local-dev only)."""
    print(
        "[daysurface-mcp] stdio transport is legacy; "
        "prefer `daysurface-serve` and connect via streamable HTTP at /mcp.",
        file=sys.stderr,
    )
    server = build_mcp_server()
    server.run(transport="stdio")


# Populate the singleton at import time so tests / direct importers that reach
# into ``mcp._tool_manager`` see registered tools without an explicit build call.
build_mcp_server()
