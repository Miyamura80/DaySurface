#!/usr/bin/env python3
"""Generate the default social-share image: public/og.png (1200x630).

Dev-only helper (the committed PNG is what ships). The Railway build runs
`bun run build`, which does NOT run this - regenerate locally after editing the
brand copy, then commit the new public/og.png:

    uv run --with pillow --with cairosvg python scripts/gen-og.py

Colors mirror the @theme tokens in src/styles/global.css and the copy mirrors
src/config/landing.ts, so the card stays on-brand with the rest of the site.
The brand mark is rasterized from the canonical public/favicon.svg (cairosvg);
if cairosvg is unavailable it falls back to a plain cyan square.
"""

from __future__ import annotations

import contextlib
import io
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- brand tokens (keep in sync with src/styles/global.css @theme) ----------
BG = (0, 0, 0)
GRID = (20, 20, 20)
FG = (255, 255, 255)
FG_MUTED = (155, 164, 166)
ACCENT = (195, 255, 253)  # Core Cyan 500

# --- copy (keep in sync with src/config/landing.ts) -------------------------
EYEBROW = "MCP SERVER STARTER"
WORDMARK = "GmailMCP"
HEADLINE = ["Give your AI agent", "real tools."]
SUBHEAD = "One service registry, exposed identically over CLI, MCP, and HTTP."
PILLS = ["CLI", "MCP", "HTTP"]
REPO = "github.com/Miyamura80/MCP-Template"

W, H = 1200, 630
PAD = 80

ARCHIVO_URL = (
    "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth,wght%5D.ttf"
)
FONT_CACHE = Path("/tmp/Archivo-variable.ttf")


def archivo(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    if not FONT_CACHE.exists():
        urllib.request.urlretrieve(ARCHIVO_URL, FONT_CACHE)
    font = ImageFont.truetype(str(FONT_CACHE), size)
    # Archivo axes are [Weight, Width]; pin width to 100 (normal).
    with contextlib.suppress(OSError):
        font.set_variation_by_axes([weight, 100])
    return font


def brand_mark(size: int) -> Image.Image | None:
    """Rasterize the canonical favicon.svg to a square RGBA mark, or None."""
    svg = Path(__file__).resolve().parent.parent / "public" / "favicon.svg"
    try:
        import cairosvg  # noqa: PLC0415 - optional dep, dev-only helper

        png = cairosvg.svg2png(
            url=str(svg), output_width=size * 2, output_height=size * 2
        )
    except (ImportError, OSError):
        return None
    return (
        Image.open(io.BytesIO(png)).convert("RGBA").resize((size, size), Image.LANCZOS)
    )


def draw_tracked(draw, xy, text, font, fill, tracking):
    """Draw text with manual letter-spacing (Pillow has no native tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Faint grid, matching the hero's grid-bg.
    for gx in range(0, W, 60):
        d.line([(gx, 0), (gx, H)], fill=GRID, width=1)
    for gy in range(0, H, 60):
        d.line([(0, gy), (W, gy)], fill=GRID, width=1)

    # Top row: brand lockup (canonical mark + wordmark) left, eyebrow right.
    mark_size = 64
    mark = brand_mark(mark_size)
    row_y = PAD - 8
    wm_font = archivo(40, 700)
    if mark is not None:
        img.paste(mark, (PAD, row_y), mark)
        wm_x = PAD + mark_size + 22
    else:
        # Fallback: the hero's cyan square if the SVG can't be rasterized.
        sq = 16
        d.rectangle([PAD, row_y + 8, PAD + sq, row_y + 8 + sq], fill=ACCENT)
        wm_x = PAD + sq + 18
    d.text((wm_x, row_y + 14), WORDMARK, font=wm_font, fill=FG)

    eb_font = archivo(20, 600)
    eb_w = sum(d.textlength(c, font=eb_font) + 4 for c in EYEBROW) - 4
    draw_tracked(d, (W - PAD - eb_w, row_y + 24), EYEBROW, eb_font, FG_MUTED, 4)

    # Headline.
    hl_font = archivo(86, 800)
    y = 168
    for line in HEADLINE:
        d.text((PAD, y), line, font=hl_font, fill=FG)
        y += 96

    # Subhead.
    sh_font = archivo(30, 400)
    d.text((PAD, y + 24), SUBHEAD, font=sh_font, fill=FG_MUTED)

    # Bottom row: transport pills (left) + repo (right).
    pill_font = archivo(26, 600)
    px = PAD
    py = H - PAD - 44
    for label in PILLS:
        tw = d.textlength(label, font=pill_font)
        pw = tw + 44
        d.rounded_rectangle(
            [px, py, px + pw, py + 48], radius=8, outline=ACCENT, width=2
        )
        d.text((px + 22, py + 8), label, font=pill_font, fill=ACCENT)
        px += pw + 16

    repo_font = archivo(24, 500)
    rw = d.textlength(REPO, font=repo_font)
    d.text((W - PAD - rw, py + 12), REPO, font=repo_font, fill=FG_MUTED)

    out = Path(__file__).resolve().parent.parent / "public" / "og.png"
    img.save(out, "PNG")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
