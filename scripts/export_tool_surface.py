"""Export the LLM-facing MCP tool surface to a committed JSON snapshot.

The landing page advertises the server's `tools[]` in its static SEP-2127 card,
but the *source of truth* for that list is the Python `@service` registry - not
hand-maintained marketing config. This script snapshots the registry (the same
list `api_server` serves at `/.well-known/mcp/server-card.json`) into

    landing-page/src/config/tool-surface.generated.json

which `landing-page/scripts/gen-discovery.ts` reads at build time. The prek
hook regenerates it automatically whenever Python changes; you can also run it
by hand:

    make gen_tool_surface     # or: uv run python scripts/export_tool_surface.py

The output is committed so the bun/Astro landing build needs no Python at build
time (mirroring the committed `openapi.json` / `server-card.json` snapshots).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from mcp_server.server import llm_tool_surface

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "landing-page" / "src" / "config" / "tool-surface.generated.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Still write changes, but exit non-zero when any were made. Used by the pre-commit hook.",
    )
    args = parser.parse_args()

    tools = [
        {"name": entry.name, "description": entry.description}
        for entry in llm_tool_surface()
    ]
    content = json.dumps(tools, indent=2) + "\n"
    previous = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else None
    changed = content != previous

    rel = OUT_PATH.relative_to(REPO_ROOT)
    # Only write when the content actually changed: the hook fires on every
    # services/ commit, but most don't touch a tool name/description, so this
    # avoids needlessly rewriting (and bumping the mtime of) a generated file.
    if changed:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(content, encoding="utf-8")
        print(f"✓ wrote {len(tools)} tools to {rel}")
    else:
        print(f"✓ {rel} already up to date ({len(tools)} tools)")

    if args.check and changed:
        print(
            "tool-surface snapshot was regenerated; stage it and commit again.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
