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
import type { Subprocess } from "bun";

const PORT = 8123;
const BASE = `http://127.0.0.1:${PORT}`;

let server: Subprocess;

beforeAll(async () => {
  if (!existsSync("dist/index.html")) {
    throw new Error("dist/ is missing - run `bun run build` before `bun test`");
  }
  server = Bun.spawn(["bun", "server.ts"], {
    env: { ...process.env, PORT: String(PORT) },
    stdout: "ignore",
    stderr: "ignore",
  });
  // Poll rather than sleep a fixed interval, so a slow machine doesn't flake.
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${BASE}/`);
      return;
    } catch {
      await Bun.sleep(50);
    }
  }
  throw new Error("server did not start");
});

afterAll(() => server?.kill());

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

describe("intent aliases answer the signup question", () => {
  const aliases = ["/signup", "/sign-up", "/register", "/login", "/get-started", "/account"];

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
    expect(html.length).toBeLessThan(60_000);
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
    expect(body).toContain("<loc>https://daysurface.com/connect</loc>");
    expect(body).not.toContain("<loc>https://daysurface.com/signup</loc>");
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
