#!/usr/bin/env python3
"""Generate the social-share images in public/ (1200x630 each).

Each card is a screenshot of a real, fully-rendered OG page (src/pages/og/
[card].astro) - the same hero chat mock the homepage shows, on the brand
black-grid background - rather than a hand-drawn approximation. One default card
plus a per-page card for the editorial pages that have their own headline
(/product, /story).

Dev-only helper (the committed PNGs are what ship). The Railway build runs
`bun run build`, which does NOT run this - regenerate locally after editing the
brand copy or the hero mock, then commit the new PNGs:

    cd landing-page
    bun install                       # once, if node_modules is missing
    uv run --with playwright python scripts/gen-og.py

The script builds the static site (`bun run build`) if `dist/` is stale/missing,
serves it on a local port, and screenshots each `#artboard` with Chromium under
prefers-reduced-motion (which freezes the mock to its static frame - the curated
inbox list + composer, no scroll/pointer loops).

Chromium comes from Playwright. In the Claude Code cloud sandbox a browser is
preinstalled under $PLAYWRIGHT_BROWSERS_PATH; elsewhere run
`uv run --with playwright playwright install chromium` first.
"""

from __future__ import annotations

import glob
import http.server
import os
import socketserver
import subprocess
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PUBLIC = ROOT / "public"

W, H = 1200, 630

# route param -> output filename. Keep in sync with the CARDS map in
# src/pages/og/[card].astro.
CARDS = {
    "default": "og.png",
    "product": "og-product.png",
    "story": "og-story.png",
}


def chromium_executable() -> str | None:
    """Locate a preinstalled Chromium, or None to use Playwright's default."""
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for pattern in (
        f"{base}/chromium-*/chrome-linux/chrome",
        f"{base}/chromium_headless_shell-*/chrome-linux/headless_shell",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def ensure_built() -> None:
    """Build the static site if the OG pages are missing from dist/."""
    if (DIST / "og" / "default" / "index.html").exists():
        return
    if not (ROOT / "node_modules").exists():
        print("node_modules missing - running `bun install`...")
        subprocess.run(["bun", "install"], cwd=ROOT, check=True)
    print("dist/ stale - running `bun run build`...")
    subprocess.run(["bun", "run", "build"], cwd=ROOT, check=True)


def serve_dist() -> tuple[socketserver.TCPServer, int]:
    """Start a background static server rooted at dist/, return (server, port)."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DIST), **kwargs)

        def log_message(self, *_args):  # noqa: D401 - silence request logging
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def render_all() -> None:
    ensure_built()
    httpd, port = serve_dist()
    executable = chromium_executable()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable)
            page = browser.new_page(
                viewport={"width": W, "height": H},
                device_scale_factor=2,
                reduced_motion="reduce",
            )
            for card, out in CARDS.items():
                page.goto(
                    f"http://127.0.0.1:{port}/og/{card}/", wait_until="networkidle"
                )
                page.evaluate("document.fonts.ready")
                artboard = page.locator("#artboard")
                artboard.wait_for(state="visible")
                dest = PUBLIC / out
                artboard.screenshot(path=str(dest))
                print(f"wrote {dest} ({dest.stat().st_size} bytes)")
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    render_all()
