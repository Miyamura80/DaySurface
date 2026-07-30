/**
 * Static server for the landing page with `Accept: text/markdown` content
 * negotiation (see acceptmarkdown.com). The site is a static Astro build, so we
 * wrap `sirv`: GET/HEAD on a route with a markdown twin, sent with `Accept:
 * text/markdown` ranked >= `text/html`, gets the markdown; everything else is
 * served as static files. Negotiable responses send `Vary: Accept,
 * Accept-Encoding` so CDNs don't cross-serve the HTML and markdown variants.
 * Run with bun: `bun server.ts`. Honors `$PORT`.
 */
import { readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import sirv from "sirv";

import { isDocsPath, proxyDocs } from "./docs-proxy.ts";

import { buildAgentsMd, build404Md } from "./src/agent/content.ts";
import { comparison, connectAliases, site } from "./src/config/landing";

const PORT = Number(process.env.PORT ?? 8080);

// NO `single: true`. That SPA fallback answered every unknown path with a 200
// and the full 272KB homepage - so /signup, /login and /register all returned
// byte-identical HTML, and an agent probing for a signup flow could not tell a
// missing route from a client-rendered one. It concluded the site was not
// agent-readable. The site is fully static: there is no client-side router for
// a fallback to serve, so unknown paths now 404 (see `notFound` below).
//
// sirv@3 dropped the `cors` option, so `setHeaders` re-adds the
// `Access-Control-Allow-Origin: *` that sirv-cli's `--cors` gave us (registries
// fetch /.well-known cross-origin, SEP-2127).
const assets = sirv("dist", {
  gzip: true,
  brotli: true,
  setHeaders: (res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
  },
});

/**
 * Highest q-value the Accept header assigns to `type`, considering `type/*`
 * and `*\/*` wildcards. Returns -1 when nothing matches. Per RFC 9110 a media
 * range with no explicit `q` has q=1.
 */
function quality(accept: string, type: string): number {
  const [t, sub] = type.split("/");
  let best = -1;
  for (const range of accept.split(",")) {
    const parts = range.trim().split(";");
    const media = parts[0]?.trim().toLowerCase();
    if (!media) continue;
    let q = 1;
    for (const param of parts.slice(1)) {
      const [k, v] = param.split("=").map((s) => s.trim());
      if (k.toLowerCase() === "q") q = Number.parseFloat(v) || 0;
    }
    const [mt, ms] = media.split("/");
    const matches =
      media === type ||
      media === "*/*" ||
      (mt === t && ms === "*") ||
      (mt === "*" && ms === sub);
    if (matches && q > best) best = q;
  }
  return best;
}

/**
 * True when the client named `text/markdown` explicitly (not via a `*\/*`
 * catch-all) and ranks it >= `text/html`. Keeps browsers and bare `curl`
 * (`*\/*`) on HTML while honoring agents that send `Accept: text/markdown`.
 */
function wantsMarkdown(accept: string | undefined): boolean {
  if (!accept) return false;
  const md = explicitQuality(accept, "text/markdown");
  if (md <= 0) return false;
  return md >= quality(accept, "text/html");
}

/** Like `quality`, but ignores wildcard ranges - the type must be named. */
function explicitQuality(accept: string, type: string): number {
  let best = -1;
  for (const range of accept.split(",")) {
    const media = range.trim().split(";")[0]?.trim().toLowerCase();
    if (media !== type) continue;
    const qMatch = range.match(/;\s*q=([^;]+)/i);
    const q = qMatch ? Number.parseFloat(qMatch[1]) || 0 : 1;
    if (q > best) best = q;
  }
  return best;
}

/** Canonical URL = site root. Query string and trailing slash are ignored. */
function isCanonical(pathname: string): boolean {
  return pathname === "/" || pathname === "/index.html";
}

/**
 * HTML routes that have a markdown twin in `dist`, and the file that answers
 * for them. `/` is absent because it is built on the fly from `buildAgentsMd`.
 *
 * The point is that an agent which has already found the page it wants can read
 * it without the surrounding 250KB of Tailwind markup - and without knowing to
 * go back and look for a site-wide index first. Advertised per page as
 * `<link rel="alternate" type="text/markdown">` (see Base.astro).
 */
const MARKDOWN_TWINS: Readonly<Record<string, string>> = {
  "/connect": "/connect.md",
  "/pricing": "/pricing.md",
  "/compare": "/compare.md",
  "/api": "/api.md",
  // Every /connect alias answers with the same document its HTML twin shows.
  ...Object.fromEntries(connectAliases.map((a) => [`/${a}`, "/connect.md"])),
  ...Object.fromEntries(
    comparison.competitors.map((c) => [`/vs/${c.id}`, `/vs/${c.id}.md`]),
  ),
};

/** Strip a trailing slash (but keep the root) so `/connect/` matches `/connect`. */
function normalize(pathname: string): string {
  return pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
}

/** First entry of a comma-joined or repeated header (e.g. `X-Forwarded-*`). */
function firstHeaderToken(value: string | string[] | undefined): string | undefined {
  if (!value) return undefined;
  const raw = Array.isArray(value) ? value[0] : value;
  const first = raw.split(",")[0]?.trim();
  return first || undefined;
}

/** Public origin for absolute links in the markdown body; forwarded host wins. */
function originFor(host: string | undefined, proto: string | undefined): string {
  if (host) return `${proto || "https"}://${host}`;
  return new URL(site.url).origin;
}

const VARY = "Accept, Accept-Encoding";

/** Fold `Accept` into any `Vary` sirv sets (it emits `Vary: Accept-Encoding`). */
function ensureVaryAccept(res: ServerResponse): void {
  const original = res.setHeader.bind(res);
  res.setHeader = ((name: string, value: number | string | readonly string[]) => {
    if (String(name).toLowerCase() === "vary") {
      const tokens = new Set(
        String(value)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      );
      tokens.add("Accept");
      return original("Vary", [...tokens].join(", "));
    }
    return original(name, value as never);
  }) as typeof res.setHeader;
}

const server = createServer((req, res) => {
  const method = req.method ?? "GET";
  // Parse the path against a fixed base (the untrusted forwarded host can't
  // affect it, nor crash the handler). On failure leave pathname empty so a
  // malformed target is non-canonical and falls through to static serving.
  let pathname = "";
  try {
    pathname = new URL(req.url ?? "/", "http://localhost").pathname;
  } catch {
    pathname = "";
  }

  // Ahead of everything else, so a docs URL is never resolved against `dist`
  // and answered as a 404 by the landing page.
  if (isDocsPath(pathname)) {
    // proxyDocs never rejects - it reports failures on `res` itself. An escaping
    // rejection here would kill the process serving the whole marketing site.
    void proxyDocs(req, res);
    return;
  }

  const readable = method === "GET" || method === "HEAD";
  const normalized = normalize(pathname);
  const twin = MARKDOWN_TWINS[normalized];
  const negotiable = readable && (isCanonical(pathname) || twin !== undefined);

  if (negotiable && wantsMarkdown(req.headers.accept)) {
    if (twin) {
      // Serve the twin from disk through sirv so it keeps compression and
      // caching. The URL the client asked for is unchanged; only the file is.
      req.url = twin;
      ensureVaryAccept(res);
      res.setHeader("Vary", VARY);
      assets(req, res, () => notFound(req, res, normalized));
      return;
    }
    // `/` has no file twin - its markdown is the agent guide, built per request
    // so the absolute links carry the forwarded host.
    sendMarkdown(req, res, buildAgentsMd(originOf(req)), 200);
    return;
  }

  // Only negotiable routes vary on Accept; scope it here so static assets keep a
  // single cache key. Seed `Vary` and fold `Accept` into sirv's later set.
  if (negotiable) {
    ensureVaryAccept(res);
    res.setHeader("Vary", VARY);
  }

  assets(req, res, () => notFound(req, res, normalized));
});

/** Public origin for absolute links, honoring the proxy's forwarded headers. */
function originOf(req: IncomingMessage): string {
  const host = firstHeaderToken(req.headers["x-forwarded-host"] ?? req.headers.host);
  const proto = firstHeaderToken(req.headers["x-forwarded-proto"]);
  return originFor(host, proto);
}

/** Send a markdown body with the negotiation headers a variant response needs. */
function sendMarkdown(
  req: IncomingMessage,
  res: ServerResponse,
  body: string,
  status: number,
): void {
  const buf = Buffer.from(body, "utf-8");
  res.statusCode = status;
  res.setHeader("Content-Type", "text/markdown; charset=utf-8");
  res.setHeader("Content-Length", String(buf.byteLength));
  res.setHeader("Vary", VARY);
  res.setHeader("Cache-Control", "public, max-age=0, must-revalidate");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.end(req.method === "HEAD" ? undefined : buf);
}

// Read once at boot: the 404 page is static and this path is hot enough that a
// per-request read would be wasted syscalls. A missing file is not fatal - the
// plain-text fallback still carries the pointer to /connect.
const NOT_FOUND_HTML: string = (() => {
  try {
    return readFileSync("dist/404.html", "utf-8");
  } catch {
    return `<!doctype html><meta charset="utf-8"><title>Not found</title><h1>No such page</h1><p>See <a href="/connect">/connect</a>.</p>`;
  }
})();

/**
 * A real 404, carrying the route map rather than just the status.
 *
 * A wrong guess should still advance the task: an agent that probes
 * `/create-account` gets told, in the body, that there is no account to create
 * and where the connect instructions are. Markdown for clients that asked for
 * it, HTML otherwise.
 */
function notFound(req: IncomingMessage, res: ServerResponse, pathname: string): void {
  res.setHeader("Access-Control-Allow-Origin", "*");
  if (wantsMarkdown(req.headers.accept)) {
    sendMarkdown(req, res, build404Md(originOf(req), pathname), 404);
    return;
  }
  const buf = Buffer.from(NOT_FOUND_HTML, "utf-8");
  res.statusCode = 404;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Content-Length", String(buf.byteLength));
  res.setHeader("Vary", VARY);
  res.end(req.method === "HEAD" ? undefined : buf);
}

server.listen(PORT, "0.0.0.0", () => {
  // eslint-disable-next-line no-console
  console.log(`landing-page serving dist/ on http://0.0.0.0:${PORT}`);
});
