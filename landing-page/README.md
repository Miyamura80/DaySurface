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
  config/landing/purpose.ts  # ← "What <product> does" + Gmail permissions (see below)
  styles/global.css      # ← design tokens (@theme)
  layouts/Base.astro     # <head>, meta, OG/Twitter tags
  components/            # one component per page section
  pages/index.astro      # assembles the sections in order
scripts/gen-og.py        # ← regenerates public/og.png (dev-only)
public/favicon.svg
public/og.png            # ← social-share card (committed, 1200×630)
```

Sections, in order: Nav → Hero → Purpose → TrustStrip → GetStarted → Features → Testimonials → Pricing → AskAi → Faq → FinalCta → Footer.

## The purpose block (`src/config/landing/purpose.ts`)

Google's OAuth verification checks that the homepage explains the app's purpose
and names the app exactly as the OAuth consent screen does. That statement -
what the product does, which Gmail permissions it asks for, and what happens to
message content - lives in `purpose.ts` and is rendered verbatim in **six**
places from that one source:

- `Purpose.astro`, directly under the hero (the human-visible answer);
- the first two FAQ entries, which also become schema.org `FAQPage` JSON-LD;
- the `SoftwareApplication` JSON-LD `description` on `/`;
- `llms.txt`, `llms-full.txt`, `agents.md`, `auth.md`, `skills.sh` and the
  `?mode=agent` view (all via `src/agent/content.ts`);
- the WebMCP `describe_product` tool in `WebMcp.astro`.

Two copies live outside this build and don't follow `purpose.ts` automatically:

- `../skills/daysurface/SKILL.md` - the repo-root source of truth for the agent
  skill. Edit it there, then `make sync-skills` (from the repo root) mirrors it
  into `public/.well-known/agent-skills/` and refreshes the `digest` in
  `index.json`. Never hand-edit the mirror; a pre-commit hook fails on drift.
- `public/.well-known/mcp.json` - hand-maintained; update it in place.

Every claim in this block is a user-facing disclosure - keep it true of the
shipped OAuth scopes and consistent with `/privacy`.
