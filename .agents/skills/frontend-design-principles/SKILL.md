---
name: frontend-design-principles
description: Core visual principles for frontend work - subtract text, prefer SVG over prose, detail behind summary, one CTA. Use when building or editing UI, React components, pages, dashboards, docs or marketing sections, modals, or reviewing a frontend diff.
---

# Frontend design principles

Apply to every UI change.

**1. Subtract.** "Perfection is when there is nothing left to subtract." Before
adding anything, delete something: helper text, headings, borders, badges,
duplicated labels.

**2. A picture beats a thousand words.** If an icon or diagram can say it, don't
write the paragraph. Reuse below first, then https://svgl.app or
https://eito.me/icons. Inline it.

**3. Detail → summary.** Show the summary only; reveal detail on hover, click,
or expand. Never dump the full record upfront. Hover alone never counts: the
same detail must open by keyboard and by touch, so lean on native
`details`/`summary` or a real button before inventing a hover affordance.

**4. Keep the main thing the main thing.** Exactly one CTA per view, with the
only high-contrast treatment on screen. Everything else stays dull, so contrast
itself points the eye. Same CTA color, shape, and placement app-wide.

## Reuse before creating

This repo has three separate frontends with no shared bundle between them, so
"reuse" means reuse *within* a surface.

**Landing page (Astro + Tailwind v4), `landing-page/`**

- Client and agent marks: `public/logos/*.svg` (claude, chatgpt, codex, goose,
  cursor, vscode, perplexity, github, mcp, cli, api, openclaw). Reference as
  `/logos/<name>.svg` with `<img>`, or as a CSS `mask` when the mark has to take
  the surrounding text color (see `src/components/Nav.astro`).
- Chat-client chrome already exists in `src/components/chat/`: `ClaudeShell`,
  `ChatGptShell`, `GooseShell`, `VsCodeShell`. Reuse a shell instead of
  rebuilding a client mock.
- Copy is mostly data. Page-level marketing strings live in
  `src/config/landing/` and design tokens in the `@theme` block of
  `src/styles/global.css`; reach for config and tokens before markup. It is not
  the whole story though, so grep before assuming: component-local labels,
  accessibility text, and the mock dialogue in `src/components/chat/` live in
  the component and belong there.
- Favicon: `public/favicon.svg`.

**Docs (Next.js + Fumadocs), `docs/`**

- Sidebar brand icons are inline SVG components in `components/icons.tsx`,
  registered in the `iconMap` in `lib/source.ts`, and selected per page with
  `icon: <key>` frontmatter. Add a component plus an `iconMap` entry. Never
  hot-link an external asset; the mark ships with the bundle.
- Shared page chrome: `components/ai/page-actions.tsx`, `lib/layout.shared.tsx`.

**MCP Apps (React in a sandboxed iframe), `mcp_server/apps/<app>/`**

- Each app is its own single-file Vite bundle, so styles are per-app by design:
  `src/styles.ts` in `gmail_inbox`, `settings`, and `pdf_signer`. Copy the
  style module into a new app rather than importing across apps.
- `dist/mcp-app.html` is committed. Rebuild with `make build_apps` after any
  visual change, or the shipped bundle silently keeps the old UI.
