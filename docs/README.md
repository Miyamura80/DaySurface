# docs

Next.js + [Fumadocs](https://github.com/fuma-nama/fumadocs) documentation site
for DaySurface. Uses **bun**, never npm.

Run development server:

```bash
bun install
bun run dev
```

Open http://localhost:3000/docs - **not** `/`. The app sets
`basePath: "/docs"`, so it serves nothing at the origin root.

## Serving model

The docs are a section of `daysurface.com`, not a `docs.` subdomain: one domain
accumulates all inbound links instead of splitting authority across two hosts.

```
                    daysurface.com
                          |
          +---------------+-----------------+
          |                                 |
    everything else                     /docs/*
          |                                 |
  landing-page (Astro, sirv)     reverse proxy in
  /  /compare  /vs/*  /api       landing-page/server.ts
  /sitemap.xml  /robots.txt                |
                                           v
                              docs service (this app)
                              basePath = /docs
                              /docs/*        pages
                              /docs/_next/*  assets
                              /docs/sitemap.xml
                              /docs/robots.txt
```

`basePath` is what makes the proxy a single rule: every asset, API route, and
metadata file this app serves already lives under `/docs`, so nothing can
collide with a landing-page route. The landing page needs `DOCS_UPSTREAM` set to
this service's origin (see `landing-page/railway.toml`).

Two consequences to keep in mind when editing routes:

- **Route dirs must not repeat the segment.** `app/[lang]/(docs)/[[...slug]]`
  serves `/docs/<slug>`; basePath supplies the `/docs` itself. `(docs)` is a
  route group, so it adds no URL segment.
- **Metadata is not basePath-aware.** Next prepends basePath to `<Link href>`
  automatically, but not to canonical/hreflang/OG URLs or `sitemap.ts` entries.
  Build those with `absoluteUrl()` from `lib/site.ts`, which adds it; use
  `docsPath()` from `lib/urls.ts` for in-app links.

English is served without a locale prefix (`hideLocale: "default-locale"`), so
`/docs/mcp/setup` is canonical and `/docs/en/mcp/setup` 307s to it. Other
locales keep their prefix: `/docs/ja/mcp/setup`.

## Explore

In the project, you can see:

- `lib/source.ts`: Code for content source adapter, [`loader()`](https://fumadocs.dev/docs/headless/source-api) provides the interface to access your content.
- `lib/layout.shared.tsx`: Shared options for layouts, optional but preferred to keep.
- `lib/site.ts`: canonical origin, product name, and `absoluteUrl()`.

| Route                       | Description                                       |
| --------------------------- | ------------------------------------------------- |
| `app/[lang]/(docs)`         | The documentation layout and pages.               |
| `app/api/search/route.ts`   | The Route Handler for search.                     |
| `app/sitemap.ts`            | `/docs/sitemap.xml`, all pages x all locales.     |
| `app/robots.ts`             | `/docs/robots.txt` (the apex one wins for crawlers). |
| `middleware.ts`             | i18n locale rewrite; see the matcher notes there. |

### Fumadocs MDX

A `source.config.ts` config file has been included, you can customise different options like frontmatter schema.

Read the [Introduction](https://fumadocs.dev/docs/mdx) for further details.

## Learn More

To learn more about Next.js and Fumadocs, take a look at the following
resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js
  features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.
- [Fumadocs](https://fumadocs.dev) - learn about Fumadocs
