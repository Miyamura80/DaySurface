"""``DaySurfaceMCP``: FastMCP with tool annotations and one log line per tool call.

Both hooks live on the server class so they cover every tool - registry-backed
ones from ``_tool_factory`` and app-only ones registered with ``@mcp.tool`` -
without touching each registration site.
"""

from collections.abc import Callable, Sequence
from typing import Any

from loguru import logger as log
from mcp.server.fastmcp import FastMCP
from mcp.types import AnyFunction, ContentBlock, Icon, ToolAnnotations

from mcp_server.tool_annotations import annotations_for
from src.utils.current_user import current_user

_MAX_CLIENT_LABEL = 80


class DaySurfaceMCP(FastMCP):
    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[AnyFunction], AnyFunction]:
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
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Run the tool, then log its name, caller and outcome (never arguments)."""
        succeeded = False
        try:
            result = await super().call_tool(name, arguments)
            succeeded = True
            return result
        finally:
            user = current_user()
            log.info(
                "MCP tool call {} {} client={} user={}",
                name,
                "ok" if succeeded else "error",
                self._client_label(),
                user.user_id if user is not None else "-",
            )

    def _client_label(self) -> str:
        """Best-effort caller name: MCP clientInfo, else the HTTP User-Agent.

        In stateless HTTP mode the initialize handshake is not kept between
        requests, so ``clientInfo`` is usually missing and the User-Agent
        (e.g. ``Claude-User``, ``openai-mcp/1.0.0 (ChatGPT)``) is what's left.
        """
        try:
            ctx = self.get_context()
            params = ctx.session.client_params
            request = ctx.request_context.request
        except (LookupError, ValueError):
            return "-"
        if params is not None:
            label = f"{params.clientInfo.name}/{params.clientInfo.version}"
        else:
            headers = getattr(request, "headers", None)
            label = headers.get("user-agent", "-") if headers is not None else "-"
        # Header values are attacker-controlled: keep log lines single-line.
        return "".join(ch for ch in label if ch.isprintable())[:_MAX_CLIENT_LABEL]
