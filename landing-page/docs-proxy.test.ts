/**
 * Tests for the docs reverse proxy. Run with `bun test` from `landing-page/`.
 *
 * The failure these exist to prevent: this process is the public entry point
 * for the whole domain, so any upstream fault that escapes `proxyDocs` takes the
 * marketing site down with it. An earlier revision buffered the response body
 * outside the try block, and a mid-body connection cut from the docs service
 * killed the server.
 */
import { describe, expect, test, beforeAll, afterAll } from "bun:test";
import { createServer, type Server } from "node:http";
import { connect, type AddressInfo } from "node:net";
import { gzipSync } from "node:zlib";

import { isDocsPath, proxyDocs } from "./docs-proxy.ts";

describe("isDocsPath", () => {
  test.each([
    ["/docs", true],
    ["/docs/", true],
    ["/docs/cli", true],
    ["/docs/sitemap.xml", true],
    ["/es/docs/cli", true],
    ["/ja/docs", true],
    ["/_next/static/chunk.js", true],
    ["/og/en/docs/x/og.png", true],
    ["/api/search", true],
    ["/llms-full.txt", true],
    // Root assets from docs/public, referenced by the docs icon metadata.
    // Without these the apex serves SPA-fallback HTML at a 200 instead.
    ["/favicon.ico", true],
    ["/icon-light.png", true],
    ["/icon-dark.png", true],
    // Must NOT match: these belong to the landing page.
    ["/", false],
    ["/docsomething", false],
    ["/compare", false],
    ["/api", false],
    ["/sitemap.xml", false],
    ["/robots.txt", false],
    ["/vs/composio-gmail", false],
    // A locale root without /docs is not ours.
    ["/es", false],
  ])("%s -> %s", (pathname, expected) => {
    expect(isDocsPath(pathname as string)).toBe(expected);
  });
});

/** Long enough that a stale `Content-Length` cannot coincidentally match. */
const GZIP_PLAINTEXT = `<html>${"y".repeat(5000)}</html>`;

/** Spin up a stub upstream whose behavior each test picks via the URL path. */
let upstream: Server;
let upstreamPort: number;
let originalUpstream: string | undefined;

beforeAll(async () => {
  originalUpstream = process.env.DOCS_UPSTREAM;
  upstream = createServer((req, res) => {
    if (req.url?.startsWith("/cut")) {
      // Headers, a partial body, then a hard socket destroy - the exact shape
      // that killed the process before the fix.
      res.writeHead(200, { "Content-Type": "text/html" });
      res.write("<html>partial");
      res.socket?.destroy();
      return;
    }
    if (req.url?.startsWith("/cookies")) {
      res.writeHead(200, {
        "Set-Cookie": ["a=1; Path=/", "b=2; Path=/"],
        "Content-Type": "text/plain",
      });
      res.end("ok");
      return;
    }
    if (req.url?.startsWith("/redirect")) {
      res.writeHead(307, { Location: "/docs/target" });
      res.end();
      return;
    }
    if (req.url?.startsWith("/gzipped")) {
      // An upstream that compresses despite our `accept-encoding: identity`.
      // fetch decompresses it, so both headers below go stale in transit.
      const body = gzipSync(Buffer.from(GZIP_PLAINTEXT, "utf-8"));
      res.writeHead(200, {
        "Content-Type": "text/plain",
        "Content-Encoding": "gzip",
        "Content-Length": String(body.byteLength),
      });
      res.end(body);
      return;
    }
    // `Date` alongside `Content-Length` is what every ordinary Next.js response
    // looks like, and is the pair that used to produce a duplicate `Date`.
    res.writeHead(200, {
      "Content-Type": "text/plain",
      Date: "Mon, 01 Jan 2001 00:00:00 GMT",
    });
    res.end("upstream-ok");
  });
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r));
  upstreamPort = (upstream.address() as AddressInfo).port;
  process.env.DOCS_UPSTREAM = `http://127.0.0.1:${upstreamPort}`;
});

afterAll(() => {
  upstream.close();
  // Restore rather than delete: the suite should not leak its stub upstream
  // into anything else sharing this process.
  if (originalUpstream === undefined) delete process.env.DOCS_UPSTREAM;
  else process.env.DOCS_UPSTREAM = originalUpstream;
});

/** Drive proxyDocs through a real server so we exercise req/res, not mocks. */
async function throughProxy(path: string): Promise<Response> {
  const front = createServer((req, res) => {
    void proxyDocs(req, res);
  });
  await new Promise<void>((r) => front.listen(0, "127.0.0.1", r));
  const port = (front.address() as AddressInfo).port;
  try {
    return await fetch(`http://127.0.0.1:${port}${path}`, { redirect: "manual" });
  } finally {
    front.close();
  }
}

/**
 * Same, but reading the raw bytes off the socket instead of going through
 * `fetch`.
 *
 * Required rather than fussy: a `Headers` object collapses repeated fields, so a
 * client-side view cannot see a duplicated singleton header at all. That blind
 * spot is exactly how a malformed response reached production - it looked fine
 * to every test and to `curl`, and only Cloudflare, which rejects it outright
 * and serves its own 502, ever disagreed.
 */
async function rawThroughProxy(
  path: string,
): Promise<{ head: string; headerCount: (name: string) => number }> {
  const front = createServer((req, res) => {
    void proxyDocs(req, res);
  });
  await new Promise<void>((r) => front.listen(0, "127.0.0.1", r));
  const port = (front.address() as AddressInfo).port;
  try {
    const raw = await new Promise<string>((resolve, reject) => {
      const sock = connect(port, "127.0.0.1", () => {
        sock.write(`GET ${path} HTTP/1.1\r\nHost: daysurface.com\r\nConnection: close\r\n\r\n`);
      });
      let buf = "";
      sock.setEncoding("latin1");
      sock.on("data", (chunk) => (buf += chunk));
      sock.on("error", reject);
      sock.on("close", () => resolve(buf));
    });
    const head = raw.split("\r\n\r\n")[0] ?? "";
    return {
      head,
      headerCount: (name) =>
        head
          .split("\r\n")
          .slice(1)
          .filter((line) => line.toLowerCase().startsWith(`${name.toLowerCase()}:`)).length,
    };
  } finally {
    front.close();
  }
}

describe("proxyDocs response framing", () => {
  test("sends exactly one Date header", async () => {
    // Two `Date` headers make the response malformed (singleton field, RFC 9110
    // §5.3). Cloudflare discards such a response and serves its own bare
    // `error code: 502`, which is what took daysurface.com/docs down: the
    // upstream's `Date` was forwarded on top of the one Bun generates.
    const { head, headerCount } = await rawThroughProxy("/docs/ok");
    expect(head.startsWith("HTTP/1.1 200")).toBe(true);
    expect(headerCount("date")).toBe(1);
  });

  test("does not forward the upstream's Date value", async () => {
    // The surviving `Date` must be ours, not the stub's fixed 2001 timestamp.
    const { head } = await rawThroughProxy("/docs/ok");
    expect(head).not.toContain("Mon, 01 Jan 2001 00:00:00 GMT");
  });

  test("strips content-encoding and content-length when fetch decompressed", async () => {
    // fetch hands us plaintext but keeps the headers describing the compressed
    // bytes. Forwarding either leaves the client unable to decode the body.
    const { headerCount } = await rawThroughProxy("/gzipped");
    expect(headerCount("content-encoding")).toBe(0);
    expect(headerCount("content-length")).toBe(0);
  });

  test("serves a decompressed body intact", async () => {
    const res = await throughProxy("/gzipped");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(GZIP_PLAINTEXT);
  });

  test("keeps content-length when the upstream honors identity", async () => {
    // The strip is conditional: an uncompressed response should still advertise
    // its length rather than fall back to chunked for no reason.
    const { headerCount } = await rawThroughProxy("/docs/ok");
    expect(headerCount("content-length")).toBe(1);
  });
});

describe("proxyDocs", () => {
  test("passes through a normal response", async () => {
    const res = await throughProxy("/docs/ok");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("upstream-ok");
  });

  test("preserves multiple Set-Cookie headers", async () => {
    // fumadocs' i18n middleware sets FD_LOCALE on the locale-strip redirect, so
    // collapsing repeated Set-Cookie would silently drop locale state.
    const res = await throughProxy("/cookies");
    const cookies = res.headers.getSetCookie();
    expect(cookies).toHaveLength(2);
    expect(cookies.join("|")).toContain("a=1");
    expect(cookies.join("|")).toContain("b=2");
  });

  test("passes redirects through instead of following them", async () => {
    const res = await throughProxy("/redirect");
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("/docs/target");
  });

  test("settles its promise on a completed response", async () => {
    // Not merely cosmetic: server.ts calls this fire-and-forget, so a promise
    // that only settled when the socket was torn down would retain per-request
    // listeners for the life of a keep-alive connection.
    let settled = false;
    const front = createServer((req, res) => {
      void proxyDocs(req, res).then(() => {
        settled = true;
      });
    });
    await new Promise<void>((r) => front.listen(0, "127.0.0.1", r));
    const port = (front.address() as AddressInfo).port;
    try {
      const res = await fetch(`http://127.0.0.1:${port}/docs/ok`, { keepalive: true });
      await res.text();
      await Bun.sleep(200);
      expect(settled).toBe(true);
    } finally {
      front.close();
    }
  });

  test("survives an upstream that dies mid-body", async () => {
    // The assertion that matters is not the status - it is that this process is
    // still alive on the next line.
    await throughProxy("/cut").catch(() => undefined);
    const after = await throughProxy("/docs/ok");
    expect(after.status).toBe(200);
    expect(await after.text()).toBe("upstream-ok");
  });
});
