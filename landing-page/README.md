# Landing page

A standalone, statically-built marketing landing page for the MCP server product. Built with [Astro](https://astro.build) + Tailwind v4, deployed independently on Railway.

It is **separate** from the `docs/` site (Next.js + Fumadocs) and from the Python server - its own folder, its own deploy.

## TLDR - rebrand it

The entire page is data-driven. Edit **one file** and you've reskinned the site:

```
src/config/landing.ts
```

Search that file for `TODO` to find every placeholder (product name, tagline, install command, GitHub/docs URLs, features, testimonials, FAQ, pricing). Optional sections are gated by `enabled` flags (`testimonials.enabled`, `pricing.enabled`).

Design tokens (colors, fonts, the accent) live in `src/styles/global.css` under the `@theme` block.

### Social-share image (`public/og.png`)

The `og:image` / `twitter:image` card is a committed 1200×630 PNG at `public/og.png` (the production build does **not** regenerate it). After changing the brand copy or tokens, regenerate and commit it:

```bash
uv run --with pillow --with cairosvg python scripts/gen-og.py
```

`scripts/gen-og.py` mirrors the `@theme` colors and the `landing.ts` copy, pulls the Archivo typeface at run time, and rasterizes the canonical brand mark from `public/favicon.svg`. To use a different card per page, pass `image="/my-og.png"` (and optionally `imageAlt`) to `Base.astro`.

## Develop

```bash
bun install
bun run dev        # http://localhost:4321
```

## Build & preview

```bash
bun run build      # static output → dist/
bun run preview    # preview the production build locally
```

## Deploy to Railway

This folder ships a `railway.toml`. Deploy it as **its own Railway service**:

1. New service → connect this repo.
2. Set **Root Directory** to `landing-page`.
3. Railway reads `railway.toml`: Railpack runs `bun run build`, then serves `dist/` with `sirv` on `$PORT`.

No Dockerfile needed - Railpack auto-detects the bun/Node project. Switch `builder` to `"DOCKERFILE"` in `railway.toml` only if you want a pinned nginx/caddy static serve.

> Remember to set the real origin in two places: `site` in `astro.config.mjs` and `site.url` in `src/config/landing.ts` (used for canonical + OG tags).

## Structure

```
src/
  config/landing.ts      # ← all copy & content (edit this)
  styles/global.css      # ← design tokens (@theme)
  layouts/Base.astro     # <head>, meta, OG/Twitter tags
  components/            # one component per page section
  pages/index.astro      # assembles the sections in order
scripts/gen-og.py        # ← regenerates public/og.png (dev-only)
public/favicon.svg
public/og.png            # ← social-share card (committed, 1200×630)
```

Sections, in order: Nav → Hero → TrustStrip → GetStarted → Features → Testimonials → Pricing → AskAi → Faq → FinalCta → Footer.
