#!/usr/bin/env python3
"""Assert every absolute in-content docs link points at a page that exists.

Why this exists
---------------
The docs URL prefix (`/docs`) comes from the fumadocs loader's ``baseUrl``, and
the MDX under ``docs/content`` hard-codes that prefix in ~100 links. Two edits
can silently break all of them at once:

* changing ``baseUrl`` (or adding a Next ``basePath``), which shifts what the
  prefix resolves to - this once rendered every link as ``/docs/docs/...``; and
* renaming or moving a page without updating the links that point at it.

Neither is caught by the existing checks. ``make docs_lint`` validates links
against the Next route *structure*, where ``/docs/<anything>`` is a legal route,
so a link can pass there and still 404 in the browser. This check resolves each
link to a concrete ``.mdx`` file instead.

Run: ``uv run scripts/check_docs_links.py`` (wired into ``make ci`` and prek).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = REPO_ROOT / "docs" / "content" / "docs"

# Keep in step with `languages` in docs/lib/i18n.ts.
LOCALES = ("en", "es", "ja", "zh")

# Matches href="/..." (MDX components) and ](/...) (markdown links).
LINK_RE = re.compile(r'(?:href="|\]\()(/[^"()\s#]*)')

# The prefix every docs page URL carries; must match `baseUrl` in docs/lib/source.ts.
DOCS_PREFIX = "/docs"


def page_exists(slug: str) -> bool:
    """True when `slug` (path under the docs root, no leading slash) has a page."""
    if not slug:
        return (CONTENT_DIR / "index.mdx").is_file()
    # A slug may be served by `<slug>.mdx` or by `<slug>/index.mdx`.
    return (CONTENT_DIR / f"{slug}.mdx").is_file() or (
        CONTENT_DIR / slug / "index.mdx"
    ).is_file()


def resolve(link: str) -> str | None:
    """Reduce a link to its docs-root-relative slug, or None if not a docs link."""
    path = link.rstrip("/")
    # Strip an optional locale prefix: /es/docs/cli -> /docs/cli
    for locale in LOCALES:
        if path == f"/{locale}" or path.startswith(f"/{locale}/"):
            path = path[len(locale) + 1 :] or "/"
            break
    if path == DOCS_PREFIX:
        return ""
    if not path.startswith(f"{DOCS_PREFIX}/"):
        return None  # Not a docs page link (e.g. /pricing on the landing site).
    return path[len(DOCS_PREFIX) + 1 :]


def main() -> int:
    if not CONTENT_DIR.is_dir():
        print(f"error: {CONTENT_DIR} not found", file=sys.stderr)
        return 1

    broken: list[tuple[Path, str]] = []
    checked = 0

    for mdx in sorted(CONTENT_DIR.rglob("*.mdx")):
        for link in LINK_RE.findall(mdx.read_text(encoding="utf-8")):
            slug = resolve(link)
            if slug is None:
                continue
            checked += 1
            if not page_exists(slug):
                broken.append((mdx.relative_to(REPO_ROOT), link))

    if broken:
        print(f"Broken docs links ({len(broken)} of {checked} checked):\n")
        for path, link in broken:
            print(f"  {path}: {link}")
        print(
            "\nEach link must resolve to a file under docs/content/docs/. "
            "If the docs URL prefix changed, update the links to match "
            "`baseUrl` in docs/lib/source.ts.",
            file=sys.stderr,
        )
        return 1

    print(f"Docs link check passed ({checked} links resolve).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
