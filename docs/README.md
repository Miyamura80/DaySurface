# docs

Next.js + [Fumadocs](https://github.com/fuma-nama/fumadocs) documentation site
for DaySurface. Uses **bun**, never npm.

Run development server:

```bash
bun install
bun run dev
```

Open http://localhost:3000/docs.

## Serving model

The docs are a section of `daysurface.com`, not a `docs.` subdomain: one domain
accumulates all inbound links instead of splitting authority across two hosts.

```
                    daysurface.com
                          |
          +---------------+------------------+
          |                                  |
    everything else              docs-owned prefixes
          |                                  |
  landing-page (Astro, sirv)      reverse proxy in
  /  /compare  /vs/*  /api        landing-page/docs-proxy.ts
  /sitemap.xml  /robots.txt                 |
                                            v
                               docs service (this app)
                               /docs/*        pages (English)
                               /{es,ja,zh}/docs/*
                               /_next/*  /og/*  /api/search
                               /docs/sitemap.xml
```

There is deliberately **no Next `basePath`**. The `/docs` segment comes from the
fumadocs loader's `baseUrl`, which keeps `page.url` a browser-usable path you can
drop into an href, a `fetch`, or a `<meta>` unchanged. Adding `basePath` would
make `page.url` basePath-relative, and every consumer that forgot to re-add the
prefix would silently 404 - including the ~100 hard-coded `/docs/...` links in
`content/`. `scripts/check_docs_links.py` (in `make ci`) guards exactly that.

The cost of skipping `basePath` is that this app serves several prefixes at its
own root, so the proxy forwards an explicit list. That list lives in
`PROXY_PREFIXES` and is the contract between the two services; the landing page
needs `DOCS_UPSTREAM` set to this service's origin.

English is served without a locale prefix (`hideLocale: "default-locale"`), so
`/docs/mcp/setup` is canonical and `/docs/en/mcp/setup` redirects to it. Other
locales keep their prefix: `/es/docs/mcp/setup`.

`/docs/sitemap.xml` is a hand-written route (`app/docs/sitemap.xml/route.ts`)
rather than Next's `app/sitemap.ts` convention, because that convention can only
emit at the origin root and `/sitemap.xml` there belongs to the landing page. It
must stay exempted in the `middleware.ts` matcher, or the locale rewrite sends it
to a path with no route.

## Explore

In the project, you can see:

- `lib/source.ts`: Code for content source adapter, [`loader()`](https://fumadocs.dev/docs/headless/source-api) provides the interface to access your content.
- `lib/layout.shared.tsx`: Shared options for layouts, optional but preferred to keep.
- `lib/site.ts`: canonical origin, product name, and `absoluteUrl()` for turning a `page.url` into an absolute URL for metadata.

| Route                       | Description                                       |
| --------------------------- | ------------------------------------------------- |
| `app/[lang]/docs`             | The documentation layout and pages.             |
| `app/docs/sitemap.xml`        | `/docs/sitemap.xml`, all pages x all locales.   |
| `app/api/search/route.ts`     | The Route Handler for search.                   |
| `middleware.ts`               | i18n locale rewrite; see the matcher notes.     |

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
