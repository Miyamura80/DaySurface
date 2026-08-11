"""Fail if the docs site's tool surface has drifted from the `@service` registry.

`scripts/export_tool_surface.py` already keeps the landing page's `tools[]`
honest by generating it. The docs site restates the same surface in prose that
cannot be generated - a grouped reference table in `docs/content/docs/mcp/tools.mdx`
plus a "34 tools" count repeated across the index pages and its translations.
Hand-maintained derived data drifts silently: adding one `@service` falsifies
every one of those numbers at once, and nothing in the build notices.

This script closes that gap. It checks two things against
``mcp_server.server.llm_tool_surface()`` (the registry minus the services
hidden by ``_excluded_from_default_mcp``, i.e. exactly what an MCP client sees):

1. Every tool name in the ``mcp/tools.mdx`` reference tables is a real tool, and
   every real tool appears there - no ghosts, no omissions.
2. Every "N tools" claim across ``docs/content/`` matches the real count.

Run it directly or via ``make tool_surface_docs_check`` (wired into ``make ci``).
"""

from __future__ import annotations

import pathlib
import re
import sys

from mcp_server.server import llm_tool_surface

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_CONTENT = REPO_ROOT / "docs" / "content"
TOOLS_PAGE = DOCS_CONTENT / "docs" / "mcp" / "tools.mdx"

# A tool name in the first cell of a reference-table row, e.g.
# "| `gmail_send` | Send a draft |" or "| `gmail_compose` **UI** | ... |".
FIRST_CELL_TOOL_PATTERN = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`")
# The page also carries per-tool parameter tables (`| Parameter | Type | ... |`),
# whose first cells are field names, not tools. Only tables headed by "Tool"
# describe the surface.
TOOL_TABLE_HEADER = "tool"

# "34 tools", "34 herramientas", "34 個のツール", "34 个工具" - match the number
# in front of whatever the locale calls a tool, without hardcoding each word.
# Latin locales put descriptive words in between ("The 34 Gmail, PDF, and
# webhook tools ..." in the tools page frontmatter), so allow a short run of
# them; CJK writes the count adjacent to the noun.
COUNT_PATTERNS = (
    re.compile(r"(\d+)(?:[ \t]+[A-Za-z][\w./-]*,?){0,5}[ \t]+(?:tools|herramientas)\b"),
    re.compile(r"(\d+)\s*(?:個のツール|个工具)"),
)


def _fail(message: str) -> None:
    print(f"❌ {message}", file=sys.stderr)


def _documented_tools(markdown: str) -> set[str]:
    """Tool names listed in the page's `| Tool | Description |` tables."""
    found: set[str] = set()
    in_tool_table = False
    seen_header = False

    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            in_tool_table = False
            seen_header = False
            continue
        if not seen_header:
            # First row of a table is its header; it decides whether the rows
            # below are tools or parameters.
            first_cell = line.strip().strip("|").split("|")[0].strip().lower()
            in_tool_table = first_cell == TOOL_TABLE_HEADER
            seen_header = True
            continue
        if in_tool_table:
            match = FIRST_CELL_TOOL_PATTERN.match(line.strip())
            if match:
                found.add(match.group(1))
    return found


def _check_table(expected: set[str]) -> bool:
    """Reference tables in tools.mdx must list every tool, and only real ones."""
    if not TOOLS_PAGE.is_file():
        _fail(f"{TOOLS_PAGE.relative_to(REPO_ROOT)} not found")
        return False

    documented = _documented_tools(TOOLS_PAGE.read_text(encoding="utf-8"))
    missing = expected - documented
    ghosts = documented - expected
    ok = True

    if missing:
        _fail(
            f"{TOOLS_PAGE.relative_to(REPO_ROOT)} is missing "
            f"{len(missing)} tool(s): {', '.join(sorted(missing))}"
        )
        ok = False
    if ghosts:
        _fail(
            f"{TOOLS_PAGE.relative_to(REPO_ROOT)} documents "
            f"{len(ghosts)} tool(s) that are not in the MCP surface: "
            f"{', '.join(sorted(ghosts))}"
        )
        ok = False
    return ok


def _check_counts(expected: int) -> bool:
    """Every "N tools" claim in the docs content must be the real count."""
    ok = True
    for path in sorted(DOCS_CONTENT.rglob("*.mdx")):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for pattern in COUNT_PATTERNS:
                for found in pattern.findall(line):
                    if int(found) != expected:
                        _fail(
                            f"{path.relative_to(REPO_ROOT)}:{line_no} claims "
                            f"{found} tools; the MCP surface has {expected}"
                        )
                        ok = False
    return ok


def main() -> int:
    surface = {entry.name for entry in llm_tool_surface()}

    table_ok = _check_table(surface)
    counts_ok = _check_counts(len(surface))

    if not (table_ok and counts_ok):
        print(
            "\nThe docs restate the @service registry. Update "
            "docs/content/docs/mcp/tools.mdx and the 'N tools' counts, then "
            "run `make gen_tool_surface` so the landing snapshot matches too.",
            file=sys.stderr,
        )
        return 1

    print(f"Tool surface docs check passed ({len(surface)} tools).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
