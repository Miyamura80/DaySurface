/**
 * Static server for the landing page with `Accept: text/markdown` content
 * negotiation on the canonical URL (see acceptmarkdown.com). The site is a
 * static Astro build, so we wrap `sirv`: GET/HEAD `/` with `Accept:
 * text/markdown` ranked >= `text/html` gets the page's markdown alternate
 * (`buildAgentsMd`); everything else is served as static files. Negotiable
 * responses send `Vary: Accept, Accept-Encoding` so CDNs don't cross-serve the
 * HTML and markdown variants. Run with bun: `bun server.ts`. Honors `$PORT`.
 */
import { createServer, type ServerResponse } from "node:http";
import sirv from "sirv";

import { isDocsPath, proxyDocs } from "./docs-proxy.ts";

import { buildAgentsMd } from "./src/agent/content.ts";
import { site } from "./src/config/landing";

const PORT = Number(process.env.PORT ?? 8080);

// `single` = SPA fallback to index.html. sirv@3 dropped the `cors` option, so
// `setHeaders` re-adds the `Access-Control-Allow-Origin: *` that sirv-cli's
// `--cors` gave us (registries fetch /.well-known cross-origin, SEP-2127).
const assets = sirv("dist", {
  single: true,
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

  // Ahead of everything else: sirv runs with `single: true`, so without this
  // the SPA fallback would answer every docs URL with the landing page at a
  // 200 instead of proxying it.
  if (isDocsPath(pathname)) {
    // proxyDocs never rejects - it reports failures on `res` itself. An escaping
    // rejection here would kill the process serving the whole marketing site.
    void proxyDocs(req, res);
    return;
  }

  const negotiable = isCanonical(pathname) && (method === "GET" || method === "HEAD");

  if (negotiable && wantsMarkdown(req.headers.accept)) {
    const host = firstHeaderToken(
      req.headers["x-forwarded-host"] ?? req.headers.host,
    );
    const proto = firstHeaderToken(req.headers["x-forwarded-proto"]);
    const body = buildAgentsMd(originFor(host, proto));
    const buf = Buffer.from(body, "utf-8");
    res.statusCode = 200;
    res.setHeader("Content-Type", "text/markdown; charset=utf-8");
    res.setHeader("Content-Length", String(buf.byteLength));
    res.setHeader("Vary", VARY);
    res.setHeader("Cache-Control", "public, max-age=0, must-revalidate");
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.end(method === "HEAD" ? undefined : buf);
    return;
  }

  // Only the canonical route varies on Accept; scope it here so static assets
  // keep a single cache key. Seed `Vary` and fold `Accept` into sirv's later set.
  if (negotiable) {
    ensureVaryAccept(res);
    res.setHeader("Vary", VARY);
  }

  assets(req, res, () => {
    res.statusCode = 404;
    res.end("Not found");
  });
});

server.listen(PORT, "0.0.0.0", () => {
  // eslint-disable-next-line no-console
  console.log(`landing-page serving dist/ on http://0.0.0.0:${PORT}`);
});
