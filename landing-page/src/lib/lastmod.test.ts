/**
 * Tests for the sitemap `lastmod` source. Run with `bun test` from
 * `landing-page/`.
 *
 * The regression these guard: `lastmod` used to be `new Date()`, so every deploy
 * announced that every page had changed. A feed whose dates cannot be trusted
 * gets its `lastmod` ignored wholesale, which throws away the signal from the
 * pages that genuinely did change.
 */
import { describe, expect, test } from "bun:test";

import { ROUTE_SOURCES, VS_SOURCES, lastCommitDate, lastmodTag } from "./lastmod";

describe("lastCommitDate", () => {
  test("dates a tracked path from git history", () => {
    const date = lastCommitDate(["landing-page/docs-proxy.ts"]);
    expect(date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test("returns the most recent date across several paths", () => {
    const own = lastCommitDate(["landing-page/src/pages/privacy.astro"]);
    const both = lastCommitDate([
      "landing-page/src/pages/privacy.astro",
      "landing-page/src/layouts/Base.astro",
    ]);
    // Whatever the values, querying more paths can only move the date forward.
    expect(both! >= own!).toBe(true);
  });

  test("returns undefined for a path git knows nothing about", () => {
    // Not a detail: the caller omits <lastmod> entirely on undefined, and the
    // alternative - falling back to the build clock - is the original bug.
    expect(lastCommitDate(["landing-page/no/such/path.ts"])).toBeUndefined();
  });

  test("is not simply reporting today", () => {
    // A date derived from git should be a commit date. This fails if someone
    // reintroduces a clock-based fallback while the repo has older commits.
    const date = lastCommitDate(["landing-page/src/pages/privacy.astro"]);
    const today = new Date().toISOString().slice(0, 10);
    expect(date).not.toBe(today);
  });
});

describe("lastmodTag", () => {
  test("emits an indented lastmod element", () => {
    expect(lastmodTag(["landing-page/docs-proxy.ts"])).toMatch(
      /^ {4}<lastmod>\d{4}-\d{2}-\d{2}<\/lastmod>\n$/,
    );
  });

  test("emits nothing rather than a made-up date when git cannot answer", () => {
    expect(lastmodTag(["landing-page/no/such/path.ts"])).toBe("");
    expect(lastmodTag([])).toBe("");
  });
});

describe("route sources", () => {
  test("every route resolves to a real commit date", () => {
    // A typo in a source path yields no date and silently drops <lastmod>, so
    // assert each route's source list actually matches tracked files.
    for (const [route, sources] of Object.entries(ROUTE_SOURCES)) {
      expect(lastCommitDate(sources), `no date for ${route}`).toBeDefined();
    }
    expect(lastCommitDate(VS_SOURCES)).toBeDefined();
  });

  test("/docs is dated by the docs content, not this project", () => {
    // The page is served by the Next.js docs service through the proxy; dating
    // it from landing-page sources would describe the wrong deployment.
    expect(ROUTE_SOURCES["/docs"]).toEqual(["docs/content"]);
  });
});
