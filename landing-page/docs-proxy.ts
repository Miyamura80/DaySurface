/**
 * Reverse proxy that serves the Next.js docs app from the marketing origin.
 *
 * The docs used to live on `docs.daysurface.com`. Serving them from the apex
 * instead means one domain accumulates all inbound links rather than splitting
 * authority across two hostnames.
 *
 * The docs app deliberately does NOT set a Next `basePath`; its `/docs` segment
 * comes from the fumadocs loader's `baseUrl`, which keeps `page.url` a
 * browser-usable path. The cost of that choice is paid here: the app serves a
 * handful of prefixes at its own root (`/_next`, `/og`, ...) rather than one
 * tidy namespace, so this module forwards an explicit list instead of a single
 * rule. That list is the contract between the two services - see PROXY_PREFIXES.
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import { Readable } from "node:stream";

/**
 * Origin of the docs service, e.g. `http://daysurface-docs.railway.internal:8080`.
 *
 * Read per request rather than captured at import time: a module-level snapshot
 * cannot be configured by a test that imports this file, and it silently bakes
 * in whatever the environment looked like at process start.
 */
function docsUpstream(): string | undefined {
  return process.env.DOCS_UPSTREAM?.replace(/\/$/, "");
}

/** Give up on a wedged upstream rather than pinning the connection forever. */
const UPSTREAM_TIMEOUT_MS = 30_000;

/**
 * Locales the docs app serves. Must match `languages` in `docs/lib/i18n.ts`;
 * they are separate bun projects with no shared workspace, so this is a copy.
 * A locale missing here 404s that language's docs at the apex.
 *
 * `en` is absent on purpose: `hideLocale: "default-locale"` means English docs
 * live at `/docs/...`, and `/en/docs/...` only ever redirects there.
 */
const DOCS_LOCALES = ["en", "es", "ja", "zh"] as const;

/**
 * Every path prefix owned by the docs service. Matched as an exact path or a
 * `/`-delimited ancestor, so `/docs` and `/docs/x` match but `/docsomething`
 * does not.
 *
 * `/api/search` is listed rather than `/api` because the landing page serves
 * its own `/api` page.
 */
const PROXY_PREFIXES: readonly string[] = [
  "/docs",
  ...DOCS_LOCALES.map((l) => `/${l}/docs`),
  "/_next",
  "/og",
  "/api/search",
  "/llms.mdx",
  "/llms-full.txt",
  // Root assets from `docs/public`, referenced by the icon metadata in
  // `docs/app/layout.tsx`. Next serves `public/` at its origin root, so these
  // are docs-owned paths despite not starting with `/docs`. Without them the
  // apex answers each one from `sirv`'s SPA fallback - HTML, at a 200 - and
  // every docs page loses its icon. The landing page uses `/favicon.svg`, so
  // there is nothing to collide with.
  "/favicon.ico",
  "/icon-light.png",
  "/icon-dark.png",
];

if (!docsUpstream()) {
  console.warn(
    "[landing-page] DOCS_UPSTREAM is not set - docs paths will return 503. " +
      "Set it to the docs service origin (e.g. http://daysurface-docs.railway.internal:8080).",
  );
}

/** True when `pathname` is owned by the docs service. */
export function isDocsPath(pathname: string): boolean {
  return PROXY_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

// Hop-by-hop headers are connection-scoped (RFC 9110 §7.6.1) and must not be
// forwarded.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function fail(res: ServerResponse, status: number, message: string): void {
  // The client may already have received the head if the upstream died
  // mid-body; writing again would throw and take the process down with it.
  if (res.headersSent) {
    res.destroy();
    return;
  }
  res.statusCode = status;
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.end(message);
}

/**
 * Proxy one request to the docs service.
 *
 * Never rejects: the caller is an http handler with nowhere to put an error,
 * and an unhandled rejection here would kill the process serving the whole
 * marketing site.
 */
export async function proxyDocs(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<void> {
  const upstreamOrigin = docsUpstream();
  if (!upstreamOrigin) {
    fail(res, 503, "Docs are temporarily unavailable.");
    return;
  }

  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (value === undefined || HOP_BY_HOP.has(key.toLowerCase())) continue;
    headers.set(key, Array.isArray(value) ? value.join(", ") : value);
  }
  // Ask the upstream for identity encoding. fetch transparently decompresses,
  // so a compressed upstream response would leave `content-encoding` and
  // `content-length` describing bytes we no longer hold - forcing us to strip
  // both and buffer the body to recompute the length. Declining compression on
  // this private hop lets the response stream through untouched instead.
  headers.set("accept-encoding", "identity");

  const method = req.method ?? "GET";
  const hasBody = method !== "GET" && method !== "HEAD";

  try {
    const upstream = await fetch(`${upstreamOrigin}${req.url ?? "/"}`, {
      method,
      headers,
      // Node's `stream/web` ReadableStream and the global one are structurally
      // distinct under these lib types even though they are the same object at
      // runtime, so this has to launder through `unknown`.
      body: hasBody
        ? (Readable.toWeb(req) as unknown as ReadableStream)
        : undefined,
      // Required by undici whenever a streaming body is sent.
      ...(hasBody ? { duplex: "half" } : {}),
      // Pass 3xx through to the browser: the docs app redirects for locale
      // normalization, and the address bar must agree with the canonical URL.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    } as RequestInit);

    res.statusCode = upstream.status;
    upstream.headers.forEach((value, key) => {
      // `Set-Cookie` is the one header that legitimately repeats; it is copied
      // separately below because `setHeader` would overwrite each prior value.
      if (HOP_BY_HOP.has(key.toLowerCase()) || key.toLowerCase() === "set-cookie") {
        return;
      }
      res.setHeader(key, value);
    });
    const cookies = upstream.headers.getSetCookie();
    if (cookies.length > 0) res.setHeader("set-cookie", cookies);

    if (method === "HEAD" || !upstream.body) {
      // Release the upstream connection rather than leaving the body dangling.
      await upstream.body?.cancel();
      res.end();
      return;
    }

    // Stream rather than buffer: `_next` chunks and OG images would otherwise
    // sit in memory in full, and time-to-first-byte would wait on the last byte.
    await new Promise<void>((resolve, reject) => {
      const body = Readable.fromWeb(upstream.body as never);
      const done = (err?: Error) => {
        body.off("error", done);
        res.off("error", done);
        res.off("finish", onFinish);
        res.off("close", onClose);
        if (err) reject(err);
        else resolve();
      };
      const onFinish = () => done();
      const onClose = () => {
        // If the client went away before the response finished, stop pulling
        // bytes we can no longer deliver instead of draining the upstream.
        if (!res.writableFinished) body.destroy();
        done();
      };
      body.once("error", done);
      res.once("error", done);
      res.once("finish", onFinish);
      res.once("close", onClose);
      body.pipe(res);
    });
  } catch (error) {
    // Defensive boundary: the docs service is a separate deployment that can be
    // restarting, unreachable, or cut mid-body. Nothing here may escape - this
    // process is the public entry point for the entire domain.
    console.error("[landing-page] docs proxy failed:", error);
    fail(res, 502, "Docs are temporarily unavailable.");
  }
}
