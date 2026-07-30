/**
 * Per-route `lastmod` dates for the sitemap feeds, read from git history.
 *
 * These routes are prerendered (`output: "static"`), so this runs at build time
 * and can shell out to git.
 *
 * Why not `new Date()`: stamping build time makes every deploy claim every page
 * changed. A sitemap that cries wolf on every push is worse than one with no
 * `lastmod` at all - Google's documented response to a feed whose dates it
 * cannot trust is to stop using the field, so honest signals from the pages
 * that *did* change get discarded along with the noise.
 *
 * Precision is deliberately per-route rather than per-file: each route declares
 * the sources that actually determine its rendered HTML, so a deploy that
 * touches nothing it depends on leaves its date alone.
 */
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** `landing-page/src/lib/` -> repo root. */
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

const LP = "landing-page";

const CFG = `${LP}/src/config/landing`;
const CMP = `${LP}/src/components`;

/**
 * On literally every page: the layout wrapper and the site-wide config it reads.
 * Nav and Footer are deliberately absent - `/privacy` and `/terms` render
 * neither, and `/api` has no Footer, so they are listed per route instead.
 */
const CHROME: readonly string[] = [`${LP}/src/layouts/Base.astro`, `${CFG}/site.ts`];

/** Header and footer, plus the config driving their links. */
const NAV_FOOTER: readonly string[] = [
  `${CMP}/Nav.astro`,
  `${CMP}/Footer.astro`,
  `${CFG}/nav.ts`,
];

/**
 * Sources behind each route, relative to the repo root.
 *
 * Tracks each page's *direct* imports. A component that changes only via one of
 * its own children can therefore leave a date a commit behind, which is the
 * error worth having: under-claiming costs a slightly delayed recrawl, whereas
 * over-claiming is the failure this file exists to remove.
 */
export const ROUTE_SOURCES: Record<string, readonly string[]> = {
  "/": [
    `${LP}/src/pages/index.astro`,
    // The homepage renders most of the component library, so the directory is
    // both the accurate answer here and the one that cannot fall out of date.
    CMP,
    `${CFG}/hero.ts`,
    `${CFG}/connect.ts`,
    `${CFG}/content.ts`,
    `${CFG}/comparison.ts`,
    `${CFG}/pricing.ts`,
    ...NAV_FOOTER,
    ...CHROME,
  ],
  // Served by the Next.js docs service through the reverse proxy, so its
  // freshness comes from the docs content rather than anything in this project.
  "/docs": ["docs/content"],
  "/compare": [
    `${LP}/src/pages/compare.astro`,
    `${CMP}/ComparisonTable.astro`,
    `${CMP}/PillarCards.astro`,
    `${CFG}/comparison.ts`,
    ...NAV_FOOTER,
    ...CHROME,
  ],
  "/pricing": [
    `${LP}/src/pages/pricing.astro`,
    `${CMP}/PricingTable.astro`,
    `${CFG}/pricing.ts`,
    `${CFG}/pricing-matrix.ts`,
    ...NAV_FOOTER,
    ...CHROME,
  ],
  "/api": [
    `${LP}/src/pages/api.astro`,
    `${CMP}/Nav.astro`,
    `${CFG}/nav.ts`,
    `${LP}/src/config/tool-surface.generated.json`,
    ...CHROME,
  ],
  // Base layout only - no Nav, no Footer.
  "/privacy": [`${LP}/src/pages/privacy.astro`, ...CHROME],
  "/terms": [`${LP}/src/pages/terms.astro`, ...CHROME],
};

/** Every `/vs/<slug>` page is one template plus the competitor config. */
export const VS_SOURCES: readonly string[] = [
  `${LP}/src/pages/vs/[slug].astro`,
  `${CMP}/ComparisonTable.astro`,
  `${CFG}/comparison.ts`,
  ...NAV_FOOTER,
  ...CHROME,
];

let warned = false;

/**
 * Date (`YYYY-MM-DD`) of the most recent commit touching any of `paths`.
 *
 * Returns undefined when git cannot answer - no repository in the build image,
 * or paths that are not committed yet. Callers omit `<lastmod>` in that case:
 * saying nothing is honest, whereas falling back to the build date would
 * reintroduce exactly the everything-changed-today claim this exists to avoid.
 */
export function lastCommitDate(paths: readonly string[]): string | undefined {
  // `git log -- ` with an empty pathspec means "no filter", which would return
  // the tip commit and hand every unmapped route today's date - the exact
  // everything-changed-on-deploy claim this module removes.
  if (paths.length === 0) return undefined;
  try {
    const stdout = execFileSync("git", ["log", "-1", "--format=%cs", "--", ...paths], {
      cwd: REPO_ROOT,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return stdout.trim() || undefined;
  } catch {
    // Defensive boundary: a sitemap must still build without git. One warning
    // rather than one per route, since the cause is the same for all of them.
    if (!warned) {
      warned = true;
      console.warn(
        "[landing-page] git unavailable - sitemap entries will omit <lastmod>.",
      );
    }
    return undefined;
  }
}

/** `<lastmod>` line for `paths`, or "" when git cannot date them. */
export function lastmodTag(paths: readonly string[], indent = "    "): string {
  const date = lastCommitDate(paths);
  return date ? `${indent}<lastmod>${date}</lastmod>\n` : "";
}
