#!/usr/bin/env python3
"""Generate the social-share images in public/ (1200x630 each).

One default card plus a per-page card for the editorial pages that have their
own headline (/product, /story). Dev-only helper (the committed PNGs are what
ship). The Railway build runs `bun run build`, which does NOT run this -
regenerate locally after editing the brand copy, then commit the new PNGs:

    uv run --with pillow --with cairosvg python scripts/gen-og.py

Colors mirror the @theme tokens in src/styles/global.css and the copy mirrors
src/config/landing/{hero,product,story}.ts, so the cards stay on-brand with the
rest of the site.
The brand mark is rasterized from the canonical public/favicon.svg (cairosvg);
if cairosvg is unavailable it falls back to a plain cyan square.
"""

from __future__ import annotations

import io
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- brand tokens (keep in sync with src/styles/global.css @theme) ----------
BG = (0, 0, 0)
GRID = (20, 20, 20)
FG = (255, 255, 255)
FG_MUTED = (155, 164, 166)
ACCENT = (195, 255, 253)  # Core Cyan 500

WORDMARK = "DaySurface"
REPO = "github.com/Miyamura80/DaySurface"
PILLS = ["Gmail", "MCP", "Open source"]

W, H = 1200, 630
PAD = 80


@dataclass
class Card:
    """One social card. Copy mirrors the matching page's config."""

    out: str
    eyebrow: str
    headline: list[str]
    subhead: str
    pills: list[str] = field(default_factory=lambda: PILLS)


# The default card mirrors the homepage hero; the two page cards mirror
# src/config/landing/{product,story}.ts.
CARDS = [
    Card(
        out="og.png",
        eyebrow="AN MCP SERVER FOR GMAIL",
        headline=["Triage, draft, sign -", "without leaving chat."],
        subhead="A real composer and a ranked inbox, inside any MCP client.",
    ),
    Card(
        out="og-product.png",
        eyebrow="HOW IT WORKS",
        headline=["An inbox you drive", "from inside the chat."],
        subhead="A composer and a ranked inbox, rendered in any MCP client.",
    ),
    Card(
        out="og-story.png",
        eyebrow="OUR STORY",
        headline=["Why we built", "DaySurface."],
        subhead="Agents that run your inbox - without locking you into one.",
        pills=["Portable", "Open source", "Yours to host"],
    ),
]

FONT_API = "https://fonts.googleapis.com/css2?family=Archivo:wght@{weight}"
FONT_CACHE = Path("/tmp/daysurface-fonts")


def archivo(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    """Load an Archivo weight, downloading it from Google Fonts once.

    Same source the site loads at runtime (Base.astro), so the card's type
    matches the rendered page. Kept in step with scripts/gen_brand_assets.py.
    """
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


def fit_headline(
    draw, lines: list[str], avail: int, sizes=(66, 60, 54, 50, 46)
) -> ImageFont.FreeTypeFont:
    """Largest Archivo-800 that keeps every headline line inside `avail`."""
    for size in sizes:
        font = archivo(size, 800)
        if all(draw.textlength(line, font=font) <= avail for line in lines):
            return font
    return archivo(sizes[-1], 800)


def wrap(draw, text: str, font: ImageFont.FreeTypeFont, avail: int) -> list[str]:
    """Greedy word-wrap `text` to lines no wider than `avail`."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= avail or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def render(card: Card) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Faint grid, matching the hero's grid-bg.
    for gx in range(0, W, 60):
        d.line([(gx, 0), (gx, H)], fill=GRID, width=1)
    for gy in range(0, H, 60):
        d.line([(0, gy), (W, gy)], fill=GRID, width=1)

    # Hero: the canonical seal mark, large, anchored to the right. This is the
    # card's dominant visual - the text column sits to its left.
    hero_size = 404
    hero = brand_mark(hero_size)
    hero_x = W - PAD - hero_size
    hero_y = 82
    if hero is not None:
        img.paste(hero, (hero_x, hero_y), hero)
    else:
        # Fallback: an empty Hackbox frame if the SVG can't be rasterized.
        d.rectangle(
            [hero_x, hero_y, hero_x + hero_size, hero_y + hero_size],
            outline=ACCENT,
            width=3,
        )
    # Repo caption, centred beneath the hero mark.
    repo_font = archivo(22, 500)
    rw = d.textlength(REPO, font=repo_font)
    d.text(
        (hero_x + hero_size // 2 - rw // 2, hero_y + hero_size + 18),
        REPO,
        font=repo_font,
        fill=FG_MUTED,
    )

    # Left text column runs from the margin to a gutter before the hero.
    col_w = hero_x - PAD - 44

    # Wordmark + eyebrow, stacked top-left.
    wm_font = archivo(42, 700)
    d.text((PAD, PAD), WORDMARK, font=wm_font, fill=FG)
    eb_font = archivo(20, 600)
    draw_tracked(d, (PAD, PAD + 58), card.eyebrow, eb_font, FG_MUTED, 4)

    # Headline (auto-sized to the column width).
    hl_font = fit_headline(d, card.headline, col_w)
    line_h = hl_font.size + 8
    y = 216
    for line in card.headline:
        d.text((PAD, y), line, font=hl_font, fill=FG)
        y += line_h

    # Subhead, word-wrapped to the column.
    sh_font = archivo(28, 400)
    y += 16
    for line in wrap(d, card.subhead, sh_font, col_w):
        d.text((PAD, y), line, font=sh_font, fill=FG_MUTED)
        y += sh_font.size + 8

    # Capability pills, bottom-left.
    pill_font = archivo(26, 600)
    px = PAD
    py = H - PAD - 48
    for label in card.pills:
        tw = d.textlength(label, font=pill_font)
        pw = tw + 44
        d.rounded_rectangle(
            [px, py, px + pw, py + 48], radius=8, outline=ACCENT, width=2
        )
        d.text((px + 22, py + 8), label, font=pill_font, fill=ACCENT)
        px += pw + 16

    out = Path(__file__).resolve().parent.parent / "public" / card.out
    img.save(out, "PNG")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


def main() -> None:
    for card in CARDS:
        render(card)


if __name__ == "__main__":
    main()
