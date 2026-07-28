# Google Search Console (agent access)

Gives coding agents working in this repo read access to Search Console data for
`daysurface.com`, so they can diagnose indexing and query performance for the
landing page directly.

This is **agent tooling, not a product feature.** It is deliberately *not* a
`@service` in `services/`.

## Why not a `@service`

The `@service` registry is the product's public tool surface: it auto-registers
into the FastMCP server and is generated into
`landing-page/src/config/tool-surface.generated.json`, which feeds
`public/.well-known/mcp/server-card.json` and the MCP registry listing.

```
  Agent-side (this doc)            Product-side (services/)
  ─────────────────────            ────────────────────────
  .mcp.json                        services/*.py  @service
      │                                  │
      ▼                                  ▼
  uvx mcp-search-console           mcp_server/server.py
      │                                  │
      ▼                                  ▼
  Search Console API               tool-surface.generated.json
                                         │
                                         ▼
                                   server-card.json  ──► public registry
```

Adding `gsc_*` services would advertise Search Console tools on a server whose
description is "An MCP server for Gmail". The agent-side path has no such
coupling.

## Server

[`AminForou/mcp-gsc`](https://github.com/AminForou/mcp-gsc), published to PyPI as
[`mcp-search-console`](https://pypi.org/project/mcp-search-console/). Chosen over
[`ahonn/mcp-server-gsc`](https://github.com/ahonn/mcp-server-gsc) because it runs
under `uvx` (matching this repo's tooling), and exposes URL inspection and
sitemap tools rather than search analytics alone.

Destructive tools (`add_site`, `delete_site`, `delete_sitemap`) are disabled
unless `GSC_ALLOW_DESTRUCTIVE=true`. Leave it unset.

## Setup

Steps 1-3 are manual - they need a human in the Google Cloud and Search Console
UIs. There is no API for property verification.

1. **Service account.** In Google Cloud Console, on the same project as the
   Gmail OAuth client: enable the **Google Search Console API**, create a
   service account, and download a JSON key. Save it outside the repo (or at
   `gsc-service-account.json` in the repo root - git-ignored).

2. **Verify the property.** In Search Console, verify `daysurface.com` via DNS
   TXT record, or by adding the meta tag through the `<slot name="head" />` in
   `landing-page/src/layouts/Base.astro`. Submit `/sitemap.xml`.

3. **Grant the service account access.** Search Console → Settings → Users and
   permissions → add the service-account email (`...@....iam.gserviceaccount.com`)
   as a **Full** or **Restricted** user. Without this the API returns 403 on
   every call - the key alone is not enough.

4. **Wire the client config.**

   ```bash
   cp .mcp.json.example .mcp.json
   # then set GSC_CREDENTIALS_PATH to the absolute path of the JSON key
   ```

   `.mcp.json` is git-ignored because it carries a machine-specific credential
   path. Restart the agent client to pick up the server.

5. **Verify.** Ask the agent to call `list_properties`. It should return
   `sc-domain:daysurface.com`.

## Cloud sessions

Claude Code on the web runs in a fresh container with no `.mcp.json` and no key
file. To use this from a cloud session, add the service-account JSON to the
environment's config as a secret and have the setup script write it to
`GSC_CREDENTIALS_PATH` at session start. Until then this is local-only.

## What the data can and cannot do

Read-only reporting plus indexing diagnostics. Specifically **not** available:

- **Forcing a crawl.** The Indexing API only accepts `JobPosting` and
  `BroadcastEvent` types. It cannot be used to push landing-page URLs.
- **Fresh data.** Search Analytics lags 2-3 days and retains 16 months.
  High-cardinality dimensions are sampled, and rare queries are withheld.
- **Property verification.** UI only, as above.

`inspect_url_enhanced` and `batch_url_inspection` are capped at 2,000
queries/day per property.
