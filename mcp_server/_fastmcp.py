"""``DaySurfaceMCP``: MCPServer with tool annotations and one log line per tool call.

Both hooks live on the server class so they cover every tool - registry-backed
ones from ``_tool_factory`` and app-only ones registered with ``@mcp.tool`` -
without touching each registration site.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger as log
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, Icon, InputRequiredResult, ToolAnnotations

from mcp_server.tool_annotations import annotations_for
from src.utils.current_user import current_user

_MAX_CLIENT_LABEL = 80
_CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"

_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


class DaySurfaceMCP(MCPServer):
    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[_CallableT], _CallableT]:
        """Register a tool, filling ``annotations`` from ``TOOL_HINTS`` when omitted."""
        if annotations is None and isinstance(name, str):
            annotations = annotations_for(name)
        return super().tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> CallToolResult | InputRequiredResult:
        """Run the tool, then log its name, caller and outcome (never arguments)."""
        succeeded = False
        try:
            result = await super().call_tool(name, arguments, context)
            succeeded = True
            return result
        finally:
            user = current_user()
            log.info(
                "MCP tool call {} {} client={} user={}",
                name,
                "ok" if succeeded else "error",
                _client_label(context),
                user.user_id if user is not None else "-",
            )


def _client_label(context: Context | None) -> str:
    """Best-effort caller name: MCP clientInfo, else the HTTP User-Agent.

    Handshake-era clients send ``clientInfo`` once in ``initialize``; on the
    2026-07-28 stateless wire it rides in every request's ``_meta``. Stateless
    legacy requests carry neither, so the User-Agent (e.g. ``Claude-User``,
    ``openai-mcp/1.0.0 (ChatGPT)``) is what's left. In-process calls have no
    request bound and log ``-``.
    """
    if context is None:
        return "-"
    try:
        request_context = context.request_context
    except ValueError:
        return "-"
    label = "-"
    info: Any = None
    params = getattr(request_context.session, "client_params", None)
    if params is not None:
        info = params.client_info
    elif request_context.meta:
        info = request_context.meta.get(_CLIENT_INFO_META)
    if isinstance(info, dict):
        label = f"{info.get('name', '?')}/{info.get('version', '?')}"
    elif info is not None:
        label = f"{getattr(info, 'name', '?')}/{getattr(info, 'version', '?')}"
    else:
        headers = getattr(request_context.request, "headers", None)
        if headers is not None:
            label = headers.get("user-agent", "-")
    # Header values are attacker-controlled: keep log lines single-line.
    return "".join(ch for ch in label if ch.isprintable())[:_MAX_CLIENT_LABEL]
