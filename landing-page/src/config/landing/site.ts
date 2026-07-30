/**
 * Core product identity + pre-connect registry branding.
 *
 * Edit `site` to re-brand the whole landing page: every other config module
 * derives its links and copy from these values, so this is the one place to
 * point at a real product.
 */

export const site = {
  // Product identity. `name` is also the OAuth consent screen's "App name" -
  // Google verification rejects the homepage if the two differ, so keep this
  // string identical to the Google Cloud OAuth branding and render it visibly
  // on the home page (nav, hero headline, purpose section).
  name: "DaySurface",
  tagline: "An MCP server for Gmail",
  // Used for <title>, meta description, and OG tags.
  description:
    "An MCP server for Gmail: triage a ranked inbox, draft replies in a real composer, and fill and sign PDF attachments - inside Claude, ChatGPT, or any MCP client.",
  // TODO: the canonical deployed URL (also set `site` in astro.config.mjs).
  url: "https://daysurface.com",
  // Docs are a path on the apex, not a `docs.` subdomain, so inbound links and
  // internal links all build authority for one domain. `docs.daysurface.com`
  // should 301 here. See the `/docs` proxy in `landing-page/server.ts`.
  docsUrl: "https://daysurface.com/docs",
  githubUrl: "https://github.com/Miyamura80/DaySurface",
  // TODO: the deployed streamable-HTTP MCP endpoint users add to their client.
  // This is the URL you paste / one-click-install into Claude, Cursor, etc.
  mcpUrl: "https://mcp.daysurface.com/mcp",
  // TODO: the deployed HTTP API base URL (same backend, vanity host for REST).
  apiUrl: "https://api.daysurface.com",
  // Server name used in client configs / deep links (no spaces).
  serverName: "daysurface",
} as const;

/**
 * Who operates the Service, and where to reach us.
 *
 * Shared by /privacy, /terms, and the home-page purpose section so the legal
 * entity, address, and support address can never drift between them. Google's
 * OAuth verification cross-checks the operator and support contact shown on the
 * home page against the privacy policy and the Cloud project's branding.
 */
export const legal = {
  operator: "GPU-EVM LTD",
  operatorAddress: "3rd Floor, 86-90 Paul Street, London, England, EC2A 4NE",
  jurisdiction: "England and Wales",
  supportEmail: "support@daysurface.com",
} as const;

/**
 * Repo name parsed from `site.githubUrl` (e.g. "DaySurface"), so the badge
 * label can never drift from the URL it links to.
 */
export const repoName: string =
  site.githubUrl.replace(/\/+$/, "").split("/").pop() || site.name;

/**
 * Pre-connect registry branding (SEP-2127 Server Card + MCP registry server.json).
 *
 * `scripts/gen-discovery.ts` reads this (plus `site`) at build time and writes
 * `public/.well-known/mcp/server-card.json` and `public/server.json`. Those are
 * what MCP registries, client "add server" directories, and AI crawlers read to
 * show your server's name, icon, and description BEFORE anyone connects.
 *
 * Title, description, website, repo URL, icon, and the MCP endpoint are all
 * derived from `site` above so you brand the product in one place. The fields
 * below have no marketing-copy equivalent, so they live here. (The advertised
 * `tools[]` surface is NOT here - it is generated from the Python `@service`
 * registry into `tool-surface.generated.json`; see `scripts/gen-discovery.ts`.)
 */
export const serverCard = {
  // Reverse-DNS registry identity, exactly one slash. Usually io.github.<owner>/<repo>.
  name: "io.github.Miyamura80/DaySurface",
  // SemVer - keep in step with pyproject.toml / server.json when you release.
  version: "0.1.1",
  // Concise capability summary (<=100 chars for the registry server.json schema).
  description: "Triage a ranked inbox, draft replies, and sign PDFs - from any MCP client.",
  // repository.source value the MCP registry expects ("github" | "gitlab" | ...).
  repositorySource: "github",
} as const;
