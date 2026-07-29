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
import type { AddressInfo } from "node:net";

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

/** Spin up a stub upstream whose behavior each test picks via the URL path. */
let upstream: Server;
let upstreamPort: number;

beforeAll(async () => {
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
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("upstream-ok");
  });
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r));
  upstreamPort = (upstream.address() as AddressInfo).port;
  process.env.DOCS_UPSTREAM = `http://127.0.0.1:${upstreamPort}`;
});

afterAll(() => {
  upstream.close();
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
