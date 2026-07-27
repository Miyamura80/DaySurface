"""App-only tools - callable by MCP App frontends, hidden from the LLM by convention.

Visibility is hinted via `meta={"ui": {"visibility": ["app"]}}`. Per spec this is
not a hard guarantee - some clients may still expose these to the LLM. For hard
isolation, run a separate FastMCP instance (out of scope). See mcp_server/MCP_UI_EDGE_CASES.md A4.
"""

import importlib
import pkgutil

_discovered: bool = False


def discover_app_tools() -> None:
    """Import every app_tools.* submodule so registrations run."""
    global _discovered
    if _discovered:
        return
    for module_info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"mcp_server.app_tools.{module_info.name}")  # noqa: TID251 - app-tools auto-discovery so module-level registrations run
    _discovered = True
