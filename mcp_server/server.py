"""MCPServer (python-sdk 2.x) that registers services as MCP tools and exposes app HTML resources.

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
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger as log
from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from common import global_config
from mcp_server._tool_errors import model_facing_message
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


def _server_version() -> str:
    """Package version for ``serverInfo`` (per-result ``_meta`` on 2026-07-28)."""
    try:
        return _pkg_version("daysurface")
    except PackageNotFoundError:
        return "0.0.0"


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding allowlist for the streamable-HTTP transport.

    The SDK enables DNS-rebinding protection and, when constructed with the
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


# Protocol eras (python-sdk 2.x, spec 2026-07-28). One ``streamable_http_app``
# serves every revision; the session manager routes by the
# ``MCP-Protocol-Version`` header:
#
# - **2026-07-28 (stateless core).** No ``initialize`` handshake, no
#   ``Mcp-Session-Id``: every request is a self-contained POST carrying the
#   protocol version, client info and client capabilities in its ``_meta``
#   (``io.modelcontextprotocol/*`` keys), so any replica can serve any request.
#   Server-initiated requests (push elicitation, sampling, roots) do not exist
#   on this wire - ``ctx.elicit()`` raises ``NoBackChannelError``; input is
#   gathered with multi-round-trip requests (MRTR, ``InputRequiredResult``)
#   instead. ``server/discover`` replaces the handshake for capability lookup.
# - **2025-11-25 and earlier (handshake era).** Clients still ``initialize``
#   and the legacy transport below handles them. ``stateless_http`` only
#   affects this path (2026-07-28 traffic never reads it).
#
# ``stateless_http=True`` (the config default) keeps legacy clients working in
# the deployments DaySurface targets - Render free-tier spin-down,
# redeploys and multi-replica routing each wipe/split an in-memory session
# store, so a stateful client's next POST would fail with
# ``mcp_session_terminated``. The legacy trade-off is unchanged: in-band
# elicitation cannot complete stateless (the client's response POST lands on
# a fresh transport), so every elicitation call site gates on the client
# having DECLARED the capability AND the request having a back-channel (see
# ``EnhancedTool.can_elicit``); enhancers take their non-elicitation
# fallbacks otherwise. Legacy deployments that want host-native elicitation
# (e.g. the PDF signing confirmation dialog) must run a single replica and set
# ``server.mcp_stateless_http: false``.
#
# MCP Apps is an extension in 2026-07-28 (SEP-2133 framework, identifier
# ``io.modelcontextprotocol/ui``). An empty ``Apps()`` only advertises it under
# ``capabilities.extensions`` (server/discover + initialize); our ui://
# resources and ``_meta.ui`` tool metadata are still registered by hand below
# so the same wire shapes reach handshake-era hosts that predate extensions.
class _DaySurfaceMCPServer(MCPServer):
    """MCPServer that lets first-party failure text reach the model.

    SDK 2.x reports any non-``ToolError`` tool exception as a bare
    ``Error executing tool <name>``. Our services fail with model-facing
    messages (recovery scripts, quota/scope refusals), so a crash whose cause
    is first-party is re-labelled as a ``ToolError`` keeping its text - the v1
    wire behavior. Third-party crashes stay sanitized. See
    ``mcp_server/_tool_errors.py``. Tool functions themselves are untouched,
    so in-process callers still see the original exception types.
    """

    async def call_tool(self, name, arguments, context=None):
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as exc:
            message = model_facing_message(exc.__cause__)
            if message is None:
                raise
            raise ToolError(f"Error executing tool {name}: {message}") from exc


mcp: MCPServer = _DaySurfaceMCPServer(
    "daysurface",
    version=_server_version(),
    instructions=_MCP_INSTRUCTIONS,
    extensions=[Apps()],
)

_populated: bool = False
# Administrative / CLI-only services that are NOT auto-exposed as MCP tools.
# This is the single source of truth for that classification: the REST transport
# (api_server/routes/services.py) also consumes it - it registers NO HTTP route
# for these administrative services (config/doctor), so they are CLI-only and
# unreachable over the network, exempting only the `greet` demo (which keeps its
# ordinary `services:execute` route). Keep this public so REST can import it
# instead of duplicating the list.
EXCLUDED_DEFAULT_MCP_SERVICES = frozenset(
    {
        "config_get",
        "config_set",
        "config_show",
        "doctor",
        "doctor_fix",
        "greet",
    }
)

# Mutating webhook-management services kept OFF the default LLM (MCP) tool
# surface, but - unlike EXCLUDED_DEFAULT_MCP_SERVICES - still reachable over HTTP.
# The assistant is fed untrusted email content, so an injected instruction could
# otherwise make the model subscribe an attacker endpoint (webhook_subscribe
# also returns the signing secret) or rotate/remove a subscription - a silent
# mail-exfil primitive. Users drive these through the Settings app
# (`settings.subscribe`, an app-only tool); programmatic HTTP integrators keep
# the authenticated route with an ordinary `services:execute` key. This is
# MCP-only: it is NOT in `_cli_only_services()`, so these services keep their
# REST route. `webhook_list` (read-only) stays on the LLM surface.
LLM_HIDDEN_SERVICES = frozenset(
    {
        "webhook_subscribe",
        "webhook_unsubscribe",
        "webhook_rotate_secret",
    }
)

# Everything kept off the default LLM tool surface: the admin/CLI-only services
# plus the prompt-injection-sensitive webhook mutations. REST admin-gating reads
# only EXCLUDED_DEFAULT_MCP_SERVICES, so this wider set never widens HTTP scope.
_NOT_ON_DEFAULT_LLM_SURFACE = EXCLUDED_DEFAULT_MCP_SERVICES | LLM_HIDDEN_SERVICES


def build_mcp_server() -> MCPServer:
    """Populate the MCPServer singleton and return it. Idempotent."""
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
        if entry.name in _NOT_ON_DEFAULT_LLM_SURFACE:
            log.debug("Skipping default MCP registration for service {!r}", entry.name)
            continue
        make_tool(mcp, entry)
    _register_app_resources(mcp)

    _populated = True
    return mcp


def llm_tool_surface() -> list[ServiceEntry]:
    """Service entries this server exposes to the LLM as MCP tools.

    The registry minus ``_NOT_ON_DEFAULT_LLM_SURFACE`` - the admin/CLI-only
    defaults plus the prompt-injection-sensitive webhook mutations - i.e. exactly
    the set ``build_mcp_server`` skips. App-only tools (registered directly via
    ``@mcp.tool`` and hidden from the LLM) are not in the service registry, so
    they are excluded automatically.

    This is the LLM-facing tool surface advertised pre-connection in the
    SEP-2127 server card (``/.well-known/mcp/server-card.json``). Sorted by name
    so the committed landing-page snapshot is stable across environments.
    """
    build_mcp_server()
    surface = (e for e in get_registry() if e.name not in _NOT_ON_DEFAULT_LLM_SURFACE)
    return sorted(surface, key=lambda e: e.name)


def _register_app_resources(mcp: MCPServer) -> None:
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
    mcp: MCPServer, uri: str, html_path: Path, app_name: str
) -> None:
    @mcp.resource(uri, mime_type=_APP_MIME_TYPE, name=f"{app_name} app")
    def _read_app() -> str:
        if not html_path.exists():
            log.warning("MCP App {!r} missing build at {}", app_name, html_path)
            return f"<!-- {app_name} not built. Run `make build_apps`. -->"
        return html_path.read_text()


def mount_on(app, path: str = "/mcp") -> Starlette:
    """Mount the streamable-HTTP MCP server onto a Starlette/FastAPI app.

    ``MCPServer.streamable_http_app()`` already serves at ``/mcp`` internally,
    so we mount it at root to avoid a doubled prefix. Caller must also include
    :func:`lifespan` in the parent app's lifespan to start the session manager.

    Returns the mounted sub-app. Because the mount sits at ``/``, it - not the
    parent - receives every path the parent's own routes don't claim, so a
    caller that wants to customize the 404 for unknown paths must register the
    handler on the returned app.
    """
    if path != "/mcp":
        raise ValueError(
            "Custom mount paths are not supported; MCPServer serves at /mcp internally."
        )
    mcp = build_mcp_server()
    # Transport options live on the app builder in SDK 2.x (not the
    # constructor). ``stateless_http`` only governs handshake-era clients.
    mcp_app = mcp.streamable_http_app(
        stateless_http=global_config.server.mcp_stateless_http,
        transport_security=_transport_security(),
    )
    app.mount("/", mcp_app)
    return mcp_app


@asynccontextmanager
async def lifespan(_app):
    """Async context manager that runs the SDK's streamable-HTTP session manager.

    The parent FastAPI app must include this in its ``lifespan=`` argument or
    incoming /mcp requests will fail with "Task group is not initialized".
    """
    mcp = build_mcp_server()
    # Only exists once ``streamable_http_app()`` has run (``mount_on``).
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
