/**
 * Static server for the landing page with `Accept: text/markdown` content
 * negotiation (see acceptmarkdown.com). The site is a static Astro build, so we
 * wrap `sirv`: GET/HEAD on a route with a markdown twin, sent with `Accept:
 * text/markdown` ranked >= `text/html`, gets the markdown; everything else is
 * served as static files. Negotiable responses send `Vary: Accept,
 * Accept-Encoding` so CDNs don't cross-serve the HTML and markdown variants.
 * Run with bun: `bun server.ts`. Honors `$PORT`.
 */
import { readFileSync, readdirSync } from "node:fs";
import { sep } from "node:path";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import sirv from "sirv";

import { isDocsPath, proxyDocs } from "./docs-proxy.ts";

import { buildAgentsMd, build404Md } from "./src/agent/content.ts";
import { connectAliases, site } from "./src/config/landing";

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
 * HTML routes that have a markdown twin, discovered by scanning `dist` at boot.
 *
 * A route qualifies when the build emitted BOTH `<path>/index.html` and
 * `<path>.md`. That second condition is what keeps standalone documents out:
 * `agents.md`, `auth.md` and `llms-full.txt` have no HTML page, so `/agents` is
 * not a route and must not start answering as one.
 *
 * Derived rather than declared. The hand-maintained table this replaces was a
 * third place - after each page's `markdownPath` prop and the `.md.ts` route
 * files - that had to agree about which pages have twins, and it pulled two
 * config imports into the HTTP server purely to rebuild filenames Astro had
 * just written to disk.
 */
function discoverMarkdownTwins(root: string): Map<string, string> {
  const twins = new Map<string, string>();
  let entries: string[];
  try {
    entries = readdirSync(root, { recursive: true, encoding: "utf-8" });
  } catch {
    // No build output (e.g. a bare checkout). Static serving 404s anyway.
    return twins;
  }
  const files = new Set(entries.map((e) => e.split(sep).join("/")));
  for (const file of files) {
    if (!file.endsWith(".md")) continue;
    const route = `/${file.slice(0, -".md".length)}`;
    if (files.has(`${route.slice(1)}/index.html`)) twins.set(route, `/${file}`);
  }
  return twins;
}

const MARKDOWN_TWINS = discoverMarkdownTwins("dist");

/**
 * Alias URLs that must carry `X-Robots-Tag: noindex, follow`.
 *
 * The HTML aliases get `<meta name="robots">` from Base.astro, but their
 * markdown twins are plain files - `/signup.md` has nowhere to put a meta tag,
 * and the markdown served by content negotiation on `/signup` has no <head> at
 * all. Both were indexable duplicates of /connect.
 *
 * This has to live in the server: setting headers on the Astro `APIRoute`
 * response would be silently dropped, because a static build writes the body to
 * `dist/signup.md` and throws the Response headers away. Only `/connect` and
 * `/connect.md` stay indexable.
 */
const NOINDEX_PATHS: ReadonlySet<string> = new Set(
  connectAliases.flatMap((a) => [`/${a}`, `/${a}.md`]),
);

/** Strip a trailing slash (but keep the root) so `/connect/` matches `/connect`. */
function normalize(pathname: string): string {
  return pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
}

/**
 * Paths that 301 elsewhere on this origin. `/developers` is the URL people type
 * for developer docs; it has no page here, and `/docs` - the docs service,
 * proxied above - is what they were after.
 */
const REDIRECTS: Readonly<Record<string, string>> = { "/developers": "/docs" };

/**
 * Public origin for the absolute links inside generated markdown bodies.
 *
 * Fixed per process, NOT read from `Host`/`X-Forwarded-Host` per request. Those
 * headers are client-controlled unless every hop in front of this process
 * rewrites them, so deriving the body from them let a request choose the
 * absolute URLs in the response - and because the markdown responses only
 * `Vary` on `Accept`/`Accept-Encoding`, a shared cache had no way to keep those
 * bodies apart and could serve one request's origin to everyone else.
 *
 * Pinning it fixes both halves at once: nothing attacker-controlled reaches the
 * body, and the body is now deterministic, so the existing `Vary` is accurate.
 * Deployments that legitimately answer on another hostname (a preview deploy, a
 * self-hosted mirror) set `PUBLIC_ORIGIN` explicitly rather than having it
 * inferred from whatever arrives on the wire.
 */
export function resolvePublicOrigin(configured: string | undefined): string {
  const fallback = new URL(site.url).origin;
  if (!configured?.trim()) return fallback;
  configured = configured.trim();
  try {
    const parsed = new URL(configured);
    // Parsing is not enough. `new URL("mailto:x").origin` is the *string*
    // "null" and ftp:// yields "ftp://host", either of which would sail through
    // a try/catch and turn every generated link into `null/connect`. Only a
    // web origin can prefix the absolute URLs these documents hand to agents.
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      // eslint-disable-next-line no-console
      console.warn(
        `[landing-page] PUBLIC_ORIGIN must be http(s) (got ${configured}) - using ${fallback}`,
      );
      return fallback;
    }
    return parsed.origin;
  } catch {
    // eslint-disable-next-line no-console
    console.warn(`[landing-page] PUBLIC_ORIGIN is not a valid URL (${configured}) - using ${fallback}`);
    return fallback;
  }
}

const PUBLIC_ORIGIN: string = resolvePublicOrigin(process.env.PUBLIC_ORIGIN);

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

/**
 * The request handler, exported so tests can mount it on an ephemeral port
 * instead of racing for a fixed one (see agent-journey.test.ts).
 */
export function handleRequest(req: IncomingMessage, res: ServerResponse): void {
  const method = req.method ?? "GET";
  // Parse the path against a fixed base (the untrusted forwarded host can't
  // affect it, nor crash the handler). On failure leave pathname empty so a
  // malformed target is non-canonical and falls through to static serving.
  let pathname = "";
  // `query` is only ever a redirect's `Location`; WHATWG parsing has already
  // encoded anything that could break out of that header.
  let query = "";
  try {
    ({ pathname, search: query } = new URL(req.url ?? "/", "http://localhost"));
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

  // Ahead of `dist`, so a redirect can't also resolve to a file. Explicit
  // `Cache-Control`: a bare 301 is cached by browsers with no expiry.
  const to = REDIRECTS[normalized];
  if (to) {
    res.writeHead(301, {
      Location: `${to}${query}`,
      "Cache-Control": "public, max-age=0, must-revalidate",
    });
    res.end();
    return;
  }

  // Keyed on the path the client asked for, not the file we end up serving: the
  // twin rewrite below repoints /signup at /connect.md, which IS indexable.
  if (NOINDEX_PATHS.has(normalized)) res.setHeader("X-Robots-Tag", "noindex, follow");
  const twin = MARKDOWN_TWINS.get(normalized);
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
    // `/` has no file twin - its markdown is the agent guide, generated here.
    // Its absolute links use the fixed PUBLIC_ORIGIN, never anything off the
    // request, which is what makes this body identical for every caller and so
    // safe to cache under the `Vary` above.
    sendMarkdown(req, res, buildAgentsMd(PUBLIC_ORIGIN), 200);
    return;
  }

  // Only negotiable routes vary on Accept; scope it here so static assets keep a
  // single cache key. Seed `Vary` and fold `Accept` into sirv's later set.
  if (negotiable) {
    ensureVaryAccept(res);
    res.setHeader("Vary", VARY);
  }

  assets(req, res, () => notFound(req, res, normalized));
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
    // Say so once at boot. Silently degrading every 404 to three lines of HTML
    // for the life of the process is the kind of fault nobody notices for
    // months, and the markdown branch keeps passing its tests throughout.
    // eslint-disable-next-line no-console
    console.warn("[landing-page] dist/404.html missing - serving a minimal 404 body");
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
    sendMarkdown(req, res, build404Md(PUBLIC_ORIGIN, pathname), 404);
    return;
  }
  const buf = Buffer.from(NOT_FOUND_HTML, "utf-8");
  res.statusCode = 404;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Content-Length", String(buf.byteLength));
  // `Vary: Accept` is required, not optional: this response genuinely differs by
  // Accept, and without it a CDN could hand the markdown 404 to a browser. The
  // matching `Cache-Control` is what keeps a probed-and-missing path from
  // occupying CDN storage under two keys indefinitely.
  res.setHeader("Vary", VARY);
  res.setHeader("Cache-Control", "public, max-age=0, must-revalidate");
  res.end(req.method === "HEAD" ? undefined : buf);
}

// Only listen when run directly (`bun server.ts`); importing this module for a
// test must not bind a port.
if (import.meta.main) {
  createServer(handleRequest).listen(PORT, "0.0.0.0", () => {
    // eslint-disable-next-line no-console
    console.log(`landing-page serving dist/ on http://0.0.0.0:${PORT}`);
  });
}
