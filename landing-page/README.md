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

The `og:image` / `twitter:image` cards are committed 1200×630 PNGs in `public/` - `og.png` (default), `og-product.png`, `og-story.png` (the production build does **not** regenerate them). Each card is a real screenshot of the homepage hero chat mock (Claude shell + curated inbox) on the brand background, so the social card shows the actual product UI. After changing the brand copy or the hero mock, regenerate and commit them:

```bash
bun install                       # once, if node_modules is missing
uv run --with playwright python scripts/gen-og.py
```

`scripts/gen-og.py` builds the static site (if `dist/` is stale), serves it, and screenshots the off-site OG pages at `src/pages/og/[card].astro` with Chromium under `prefers-reduced-motion` (which freezes the mock to its static frame). The card copy lives in that page's `getStaticPaths`. To use a different card per page, pass `image="/my-og.png"` (and optionally `imageAlt`) to `Base.astro`.

> Needs Chromium via Playwright. The Claude Code cloud sandbox ships one under `$PLAYWRIGHT_BROWSERS_PATH`; elsewhere run `uv run --with playwright playwright install chromium` first.

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
src/pages/og/[card].astro # ← off-site OG artboards (hero mock, noindex)
scripts/gen-og.py        # ← screenshots them into public/og*.png (dev-only)
public/favicon.svg
public/og.png            # ← social-share card (committed, 1200×630 @2x)
```

Sections, in order: Nav → Hero → TrustStrip → GetStarted → Features → Testimonials → Pricing → AskAi → Faq → FinalCta → Footer.
