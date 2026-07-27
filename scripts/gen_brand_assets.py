#!/usr/bin/env python3
"""Generate the raster brand assets from the canonical Hackbox mark.

Dev-only helper (the committed PNGs are what ship). Nothing in CI or the
Railway build runs this - regenerate locally after a rebrand, then commit:

    uv run --with pillow --with cairosvg python scripts/gen_brand_assets.py

Outputs:
    media/banner.png            README banner (1600x500)
    docs/public/icon-light.png  docs favicon, light scheme (512x512)
    docs/public/icon-dark.png   docs favicon, dark scheme (512x512)
    docs/public/favicon.ico     docs favicon, multi-size
    docs/public/logo-light.png  wordmark lockup for light backgrounds
    docs/public/logo-dark.png   wordmark lockup for dark backgrounds

The mark is always rasterized from landing-page/public/favicon.svg so the
README, the docs and the landing page cannot drift apart - edit the SVG, then
re-run this. Colors mirror the @theme tokens in landing-page/src/styles/
global.css; the typeface is Archivo, the same face the docs and landing page
load from Google Fonts.

The social-share card is generated separately by landing-page/scripts/gen-og.py.
"""

from __future__ import annotations

import io
import re
import urllib.request
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
MARK_SVG = REPO / "landing-page" / "public" / "favicon.svg"

# --- brand tokens (keep in sync with landing-page/src/styles/global.css) ----
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAPHENE = (155, 164, 166)
CYAN = (195, 255, 253)
INK = (28, 28, 28)  # Grid Grey 1C1C1C, for wordmarks on light backgrounds

WORDMARK = "DaySurface"
TAGLINE = "An MCP server for Gmail"
ENDPOINT = "mcp.daysurface.com/mcp"

FONT_API = "https://fonts.googleapis.com/css2?family=Archivo:wght@{weight}"
FONT_CACHE = Path("/tmp/daysurface-fonts")


def archivo(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    """Load an Archivo weight, downloading it from Google Fonts once."""
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    ttf = FONT_CACHE / f"Archivo-{weight}.ttf"
    if not ttf.exists():
        # The CSS API serves a per-weight @font-face block; pull the TTF out.
        req = urllib.request.Request(
            FONT_API.format(weight=weight),
            headers={"User-Agent": "Mozilla/5.0"},  # else Google serves woff2
        )
        with urllib.request.urlopen(req) as resp:
            css = resp.read().decode()
        match = re.search(r"src: url\((https://[^)]+\.ttf)\)", css)
        if match is None:
            raise RuntimeError(f"no TTF in Google Fonts CSS for weight {weight}")
        urllib.request.urlretrieve(match.group(1), ttf)
    return ImageFont.truetype(str(ttf), size)


def mark(size: int) -> Image.Image:
    """Rasterize the canonical Hackbox mark to a square RGBA image."""
    png = cairosvg.svg2png(
        url=str(MARK_SVG), output_width=size * 2, output_height=size * 2
    )
    return (
        Image.open(io.BytesIO(png)).convert("RGBA").resize((size, size), Image.LANCZOS)
    )


def grid_markers(d: ImageDraw.ImageDraw, w: int, h: int, inset: int) -> None:
    """Tiny cyan dashes at the composition's corners - blueprint texture."""
    for x, y in (
        (inset, inset),
        (w - inset - 3, inset),
        (inset, h - inset - 3),
        (w - inset - 3, h - inset - 3),
    ):
        d.rectangle([x, y, x + 2, y + 2], fill=CYAN)


def build_banner() -> Path:
    """README banner: mark + wordmark lockup on black, 1400x440.

    Content is pinned to the four corners (mark left, endpoint readout top
    right, transport labels bottom) so the black between them reads as
    deliberate negative space rather than an unbalanced gap.
    """
    w, h = 1400, 440
    pad = 96
    img = Image.new("RGB", (w, h), BLACK)
    d = ImageDraw.Draw(img)
    grid_markers(d, w, h, 36)

    mark_size = 220
    mark_x, mark_y = pad, (h - mark_size) // 2
    m = mark(mark_size)
    img.paste(m, (mark_x, mark_y), m)

    text_x = mark_x + mark_size + 64
    d.text((text_x, 128), WORDMARK, font=archivo(92, 800), fill=WHITE)
    d.text((text_x, 244), TAGLINE, font=archivo(32, 400), fill=GRAPHENE)

    # Endpoint readout, top right - balances the mark across the diagonal.
    endpoint_font = archivo(24, 500)
    ew = d.textlength(ENDPOINT, font=endpoint_font)
    d.text((w - pad - ew, 132), ENDPOINT, font=endpoint_font, fill=GRAPHENE)

    # Square-cornered transport labels (no pills - the brand keeps corners hard).
    label_font = archivo(22, 600)
    lx, ly = text_x, h - 128
    for label in ("CLI", "MCP", "HTTP"):
        box_w = d.textlength(label, font=label_font) + 36
        d.rectangle([lx, ly, lx + box_w, ly + 40], outline=CYAN, width=2)
        d.text((lx + 18, ly + 8), label, font=label_font, fill=CYAN)
        lx += box_w + 14

    out = REPO / "media" / "banner.png"
    img.save(out, "PNG")
    return out


def build_icons() -> list[Path]:
    """Docs favicons. The mark carries its own black tile, so one art works
    on both light and dark chrome - both files are rendered from the same SVG."""
    written = []
    icon = mark(512)
    for name in ("icon-light.png", "icon-dark.png"):
        out = REPO / "docs" / "public" / name
        icon.save(out, "PNG")
        written.append(out)

    ico = REPO / "docs" / "public" / "favicon.ico"
    icon.save(ico, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    written.append(ico)
    return written


def build_logos() -> list[Path]:
    """Horizontal wordmark lockups on transparent backgrounds.

    Drawn on an oversized canvas, then cropped to the inked pixels and re-padded
    evenly, so the exported asset has predictable margins whatever the wordmark
    metrics turn out to be.
    """
    written = []
    margin = 32
    mark_size = 180
    for name, fill in (("logo-light.png", INK), ("logo-dark.png", WHITE)):
        img = Image.new("RGBA", (1400, 320), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = mark(mark_size)
        img.paste(m, (40, (320 - mark_size) // 2), m)
        d.text((40 + mark_size + 48, 106), WORDMARK, font=archivo(96, 800), fill=fill)

        bbox = img.getchannel("A").getbbox()
        cropped = img.crop(bbox)
        out_img = Image.new(
            "RGBA",
            (cropped.width + margin * 2, cropped.height + margin * 2),
            (0, 0, 0, 0),
        )
        out_img.paste(cropped, (margin, margin), cropped)

        out = REPO / "docs" / "public" / name
        out_img.save(out, "PNG")
        written.append(out)
    return written


def main() -> None:
    for path in [build_banner(), *build_icons(), *build_logos()]:
        print(f"wrote {path.relative_to(REPO)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
