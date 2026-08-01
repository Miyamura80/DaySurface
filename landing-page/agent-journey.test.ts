/**
 * Agent-journey contract tests. Run with `bun test` from `landing-page/`.
 *
 * These pin the behaviour an autonomous agent depends on when it lands on
 * daysurface.com without prior knowledge. Every assertion here failed before
 * the changes they guard, and each maps to a way the site misled a real agent:
 *
 *  - The SPA fallback answered `/signup`, `/login` and `/register` with a 200
 *    and the full 272KB homepage, so route probing could not distinguish a
 *    missing page from a client-rendered one. The agent concluded the site was
 *    not agent-readable and gave up on the site entirely.
 *  - `/llms-full.txt` was claimed by the docs proxy, so the URL advertised in
 *    llms.txt, agents.md and every page head returned a docs dump opening on
 *    "# Deployment" instead of the product overview.
 *  - The words "sign up" and "account" appeared nowhere on the site, so the
 *    answer to "how do I create an account" existed only in the GitHub README.
 *
 * They run against the built `dist/` through the real server, because most of
 * this behaviour lives in the routing, not in the pages.
 */
import { describe, expect, test, beforeAll, afterAll } from "bun:test";
import { existsSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { handleRequest, resolvePublicOrigin } from "./server.ts";
import { connectAliases } from "./src/config/landing";

// Mount the real handler on an ephemeral port rather than spawning `bun
// server.ts` on a fixed one: a fixed port races in parallel CI, and a subprocess
// that dies on boot surfaces only as a timeout with its stderr discarded.
let server: Server;
let BASE: string;

beforeAll(async () => {
  if (!existsSync("dist/index.html")) {
    throw new Error("dist/ is missing - run `bun run build` before `bun test`");
  }
  server = createServer(handleRequest);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  BASE = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

// Await the close callback: returning before the server has drained leaves it
// accepting connections until process exit, and hangs frameworks that wait on
// open handles.
afterAll(async () => {
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
});

const md = { Accept: "text/markdown" };

describe("unknown paths 404", () => {
  // The bug: `sirv({single: true})` answered all of these with the homepage at
  // a 200. An agent cannot tell "no such route" from "route needs JS" when
  // every guess returns identical HTML.
  test.each(["/nonexistent-xyz", "/app", "/dashboard", "/create-account", "/ask"])(
    "%s",
    async (path) => {
      const res = await fetch(`${BASE}${path}`);
      expect(res.status).toBe(404);
    },
  );

  test("the 404 body points at /connect rather than just saying Not Found", async () => {
    const res = await fetch(`${BASE}/create-account`, { headers: md });
    expect(res.status).toBe(404);
    const body = await res.text();
    expect(body).toContain("/connect");
    expect(body.toLowerCase()).toContain("no account to create");
  });
});

// /developers is the docs URL people type; it 404'd, while /docs is right here
// on this origin. Trailing slash and query string included - both broke the
// lookup or got dropped in earlier drafts.
describe("/developers redirects to the docs", () => {
  test.each([
    ["/developers", "/docs"],
    ["/developers/", "/docs"],
    ["/developers?utm_source=x", "/docs?utm_source=x"],
  ])("%s -> %s", async (from, to) => {
    const res = await fetch(`${BASE}${from}`, { redirect: "manual" });
    expect(res.status).toBe(301);
    expect(res.headers.get("location")).toBe(to);
  });
});

describe("intent aliases answer the signup question", () => {
  // Every alias, not a sample. This is also the guard against an alias that
  // collides with a real page (`api`, `compare`, `pricing`): Astro would let the
  // static route win and the alias would silently serve something else.
  const aliases = connectAliases.map((a) => `/${a}`);

  test.each(aliases)("%s serves the connect content at a 200", async (path) => {
    const res = await fetch(`${BASE}${path}`);
    expect(res.status).toBe(200);
    const body = await res.text();
    // The premise-refuting sentence, and the endpoint an agent needs.
    expect(body).toContain("no signup form");
    expect(body).toContain("https://mcp.daysurface.com/mcp");
  });

  test.each(aliases)("%s canonicalises to /connect and is noindex", async (path) => {
    const body = await (await fetch(`${BASE}${path}`)).text();
    // Trailing slash matters: Astro builds /connect as a directory, so this must
    // match /connect's own canonical exactly or the two split the ranking signal.
    expect(body).toContain('rel="canonical" href="https://daysurface.com/connect/"');
    expect(body).toContain('name="robots" content="noindex, follow"');
  });

  test("/connect and its aliases name the SAME canonical URL", async () => {
    const canonicalOf = async (path: string) => {
      const body = await (await fetch(`${BASE}${path}`)).text();
      return body.match(/rel="canonical" href="([^"]+)"/)?.[1];
    };
    expect(await canonicalOf("/signup")).toBe((await canonicalOf("/connect")) as string);
  });

  test("/connect itself is indexable; only the aliases are not", async () => {
    const connect = await (await fetch(`${BASE}/connect`)).text();
    expect(connect).not.toContain('name="robots"');
  });

  test("the shared setup prompt is printed once per group, not per client", async () => {
    // Four prompt targets share one ~450-char prompt. Repeating it inflated
    // connect.md by a third and /connect by ~1.5KB - on the two surfaces whose
    // whole purpose is surviving a fetcher's truncation.
    const marker = "Use THIS client's own config keys";
    const markdown = await (await fetch(`${BASE}/connect.md`)).text();
    expect(markdown.split(marker).length - 1).toBe(1);
    const html = await (await fetch(`${BASE}/connect`)).text();
    expect(html.split("Use THIS client&#39;s own config keys").length - 1).toBe(1);
  });

  test("no markdown syntax leaks into the rendered HTML page", async () => {
    // connectPage copy feeds both the HTML page and connect.md verbatim, so a
    // backtick meant as inline code renders as a literal backtick in a browser.
    const html = await (await fetch(`${BASE}/connect`)).text();
    const body = html.slice(html.indexOf("<main"), html.indexOf("</main>"));
    expect(body).not.toContain("`");
  });

  test("/signup carries EVERY client's install path, not just one", async () => {
    // We do not know which client the agent is running in, so a response that
    // only covers Claude is a failure even though it returns 200 with content.
    const body = await (await fetch(`${BASE}/signup`, { headers: md })).text();
    for (const link of [
      "claude.ai/new",
      "cursor://anysphere.cursor-deeplink",
      "vscode.dev/redirect/mcp/install",
      "goose://extension",
      "chatgpt.com/plugins",
      "claude mcp add",
    ]) {
      expect(body).toContain(link);
    }
  });

  test("dialog-only steps are labelled a fallback, not remaining work", async () => {
    // ChatGPT's `steps` are the FULL path from scratch - the deep link already
    // performs the first two. Labelling them as what is left tells the reader to
    // turn on Developer mode and click Create again, both already done.
    const body = await (await fetch(`${BASE}/connect.md`)).text();
    const idx = body.indexOf("Settings → Connectors → Advanced settings");
    expect(idx).toBeGreaterThan(-1);
    const label = body.slice(Math.max(0, idx - 120), idx);
    expect(label).toContain("if the link does not work");
    expect(body).not.toContain("The rest of the flow");
  });

  test("ChatGPT is never presented as one-click", async () => {
    // connect.ts and deeplink.ts both warn that ChatGPT's link opens an EMPTY
    // dialog. Telling an agent the install is done while the form is blank is
    // worse than shipping no link, so the grouping must keep them apart.
    const body = await (await fetch(`${BASE}/connect.md`)).text();
    const oneClick = body.slice(
      body.indexOf("## One click"),
      body.indexOf("## One command"),
    );
    expect(oneClick).not.toContain("ChatGPT");
    expect(body).toContain("Opens the dialog, but you still paste the URL");
  });
});

describe("/llms-full.txt is the product document", () => {
  test("it is not the docs corpus dump", async () => {
    const body = await (await fetch(`${BASE}/llms-full.txt`)).text();
    expect(body.startsWith("# DaySurface")).toBe(true);
    expect(body.startsWith("# Deployment")).toBe(false);
  });

  test("it answers the account question before anything else needs fetching", async () => {
    const body = await (await fetch(`${BASE}/llms-full.txt`)).text();
    expect(body).toContain("no signup form");
  });
});

describe("markdown negotiation", () => {
  test.each([
    ["/", "text/markdown"],
    ["/connect", "text/markdown"],
    ["/pricing", "text/markdown"],
    ["/compare", "text/markdown"],
    ["/api", "text/markdown"],
    ["/vs/composio-gmail", "text/markdown"],
  ])("%s serves markdown when asked", async (path, type) => {
    const res = await fetch(`${BASE}${path}`, { headers: md });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toStartWith(type as string);
    // Cross-serving the HTML and markdown variants from a CDN would undo this.
    expect(res.headers.get("vary")).toContain("Accept");
  });

  test("browsers still get HTML", async () => {
    const res = await fetch(`${BASE}/connect`, {
      headers: { Accept: "text/html,application/xhtml+xml,*/*;q=0.8" },
    });
    expect(res.headers.get("content-type")).toStartWith("text/html");
  });

  test("a bare */* client gets HTML, not markdown", async () => {
    // `curl` with no Accept header sends `*/*`; it must not flip the site to
    // markdown for every ordinary consumer.
    const res = await fetch(`${BASE}/connect`, { headers: { Accept: "*/*" } });
    expect(res.headers.get("content-type")).toStartWith("text/html");
  });
});

describe("indexing controls", () => {
  test("alias markdown twins are noindex, canonical /connect.md is not", async () => {
    // The HTML aliases get <meta name="robots"> from Base.astro, but a .md file
    // has nowhere to put one - and a static build discards headers set on the
    // Astro APIRoute, so this has to come from the server.
    for (const path of ["/signup.md", "/login.md", "/get-started.md"]) {
      const res = await fetch(`${BASE}${path}`);
      expect(res.status).toBe(200);
      expect(res.headers.get("x-robots-tag")).toBe("noindex, follow");
    }
    expect((await fetch(`${BASE}/connect.md`)).headers.get("x-robots-tag")).toBeNull();
  });

  test("markdown served by negotiation on an alias is noindex too", async () => {
    // /signup with Accept: text/markdown returns a body with no <head> at all.
    const res = await fetch(`${BASE}/signup`, { headers: md });
    expect(res.headers.get("x-robots-tag")).toBe("noindex, follow");
    expect((await fetch(`${BASE}/connect`, { headers: md })).headers.get("x-robots-tag"))
      .toBeNull();
  });
});

describe("PUBLIC_ORIGIN resolution", () => {
  const CANONICAL = "https://daysurface.com";

  test.each([
    // Unset / blank falls back to the canonical origin.
    [undefined, CANONICAL],
    ["", CANONICAL],
    ["   ", CANONICAL],
    // A real web origin is honoured, path and query stripped.
    ["https://preview.example.com", "https://preview.example.com"],
    ["http://localhost:8080/ignored?x=1", "http://localhost:8080"],
    ["  https://spaced.example.com  ", "https://spaced.example.com"],
    // Parsing alone is not enough: these all parse. `new URL("mailto:x").origin`
    // is the literal string "null", which would render every generated link as
    // `null/connect`; ftp:// yields a non-web origin just as unusable.
    ["mailto:hi@example.com", CANONICAL],
    ["javascript:alert(1)", CANONICAL],
    ["ftp://files.example.com", CANONICAL],
    ["file:///etc/passwd", CANONICAL],
    // Unparseable.
    ["not a url", CANONICAL],
  ])("%s -> %s", (configured, expected) => {
    expect(resolvePublicOrigin(configured as string | undefined)).toBe(expected);
  });

  test("never returns the string 'null' as an origin", () => {
    for (const bad of ["mailto:x@y.z", "javascript:1", "data:text/plain,hi"]) {
      expect(resolvePublicOrigin(bad)).not.toContain("null");
    }
  });
});

describe("generated markdown does not trust request headers", () => {
  test("a spoofed X-Forwarded-Host cannot choose the links in the body", async () => {
    // The body embeds absolute URLs. Deriving them from a client-controlled
    // header let a request pick them, and because these responses only Vary on
    // Accept, a shared cache could then serve that body to everyone else.
    const evil = { ...md, "X-Forwarded-Host": "evil.example.com" };
    for (const path of ["/", "/definitely-not-a-page"]) {
      const body = await (await fetch(`${BASE}${path}`, { headers: evil })).text();
      expect(body).not.toContain("evil.example.com");
      expect(body).toContain("https://daysurface.com");
    }
  });

  test("the body is identical regardless of forwarded headers", async () => {
    const plain = await (await fetch(`${BASE}/`, { headers: md })).text();
    const forwarded = await (
      await fetch(`${BASE}/`, {
        headers: { ...md, "X-Forwarded-Host": "other.example.com", "X-Forwarded-Proto": "http" },
      })
    ).text();
    expect(forwarded).toBe(plain);
  });
});

describe("payload budgets", () => {
  // The homepage is 15% visible text: ~95KB of Tailwind class attributes and
  // ~41KB of inline SVG. A fetcher that truncates raw HTML never reaches the
  // content. The budget is a ratchet - it should fall, never rise.
  test("the homepage stays under 300KB", async () => {
    const body = await (await fetch(`${BASE}/`)).text();
    expect(body.length).toBeLessThan(300_000);
  });

  test("the connect answer is small enough to survive truncation", async () => {
    const html = await (await fetch(`${BASE}/connect`)).text();
    expect(html.length).toBeLessThan(30_000);
    const markdown = await (await fetch(`${BASE}/connect.md`)).text();
    expect(markdown.length).toBeLessThan(10_000);
  });
});

describe("discovery documents", () => {
  test("install.json carries the full matrix and the no-account fact", async () => {
    const res = await fetch(`${BASE}/.well-known/mcp/install.json`);
    expect(res.status).toBe(200);
    const doc = (await res.json()) as {
      account: { required: boolean; summary: string };
      clients: { id: string; effort: string; install_url: string | null }[];
    };
    expect(doc.account.required).toBe(false);
    expect(doc.clients.length).toBeGreaterThanOrEqual(8);
    // The one-click set must actually carry URLs, or the document is decorative.
    const oneClick = doc.clients.filter((c) => c.effort === "one-click");
    expect(oneClick.length).toBeGreaterThanOrEqual(4);
    expect(oneClick.every((c) => Boolean(c.install_url))).toBe(true);
    // ChatGPT opens an empty dialog - it must never be classified one-click.
    expect(doc.clients.find((c) => c.id === "chatgpt")?.effort).toBe("dialog-only");
  });

  test("no route answers markdown unless it also has an HTML page", async () => {
    // The twin table is derived by scanning dist. A bare `agents.md` with no
    // `agents/index.html` must not make /agents into a route.
    for (const path of ["/agents", "/auth", "/llms-full"]) {
      const res = await fetch(`${BASE}${path}`, { headers: md });
      expect(res.status).toBe(404);
    }
    // ...while the real documents stay reachable at their own URLs.
    expect((await fetch(`${BASE}/agents.md`)).status).toBe(200);
    expect((await fetch(`${BASE}/auth.md`)).status).toBe(200);
  });

  test("robots.txt no longer advertises an /ask endpoint this origin lacks", async () => {
    const body = await (await fetch(`${BASE}/robots.txt`)).text();
    expect(body).not.toContain("ask endpoint: https://daysurface.com/ask");
  });

  test("llms.txt routes an agent to the connect answer", async () => {
    const body = await (await fetch(`${BASE}/llms.txt`)).text();
    expect(body).toContain("/connect.md");
    expect(body).toContain("no signup form");
  });

  test("the sitemap lists /connect but not the noindex aliases", async () => {
    const body = await (await fetch(`${BASE}/sitemap.xml`)).text();
    expect(body).toContain("<loc>https://daysurface.com/connect/</loc>");
    expect(body).not.toContain("<loc>https://daysurface.com/signup/</loc>");
  });

  test("every sitemap URL is the page's own canonical form", async () => {
    // Listing /compare while the page canonicalises to /compare/ points crawlers
    // at a non-canonical variant of every page and makes them lean on canonical
    // consolidation for something the sitemap can just state correctly.
    const sitemap = await (await fetch(`${BASE}/sitemap.xml`)).text();
    const locs = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs.length).toBeGreaterThan(5);
    for (const loc of locs) {
      const path = new URL(loc).pathname;
      // /docs is proxied to the Next.js docs service, which owns its canonical.
      if (path === "/docs") continue;
      const res = await fetch(`${BASE}${path}`);
      if (res.status !== 200) continue;
      const canonical = (await res.text()).match(/rel="canonical" href="([^"]+)"/)?.[1];
      if (canonical) expect(canonical).toBe(loc);
    }
  });
});

describe("the install matrix has exactly one source", () => {
  test("connect.md, install.json and the HTML page agree on Claude's link", async () => {
    // Three consumers previously built this independently; WebMcp.astro passed
    // `serverName` where the visible UI passed `site.name`, so agents were told
    // the connector was called `daysurface` and users saw `DaySurface`.
    const json = (await (
      await fetch(`${BASE}/.well-known/mcp/install.json`)
    ).json()) as { clients: { id: string; install_url: string | null }[] };
    const claude = json.clients.find((c) => c.id === "claude")?.install_url;
    expect(claude).toBeTruthy();

    const markdown = await (await fetch(`${BASE}/connect.md`)).text();
    expect(markdown).toContain(claude as string);

    // Astro escapes `&` as `&#38;` in some attributes and not others, so undo
    // that before comparing rather than guessing which form this URL got.
    const html = (await (await fetch(`${BASE}/connect`)).text()).replaceAll("&#38;", "&");
    expect(html).toContain(claude as string);
  });
});
