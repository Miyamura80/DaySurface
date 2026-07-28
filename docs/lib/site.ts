/**
 * Canonical identity for the docs site.
 *
 * The docs are served from the apex under `/docs` (see `basePath` in
 * `next.config.mjs` and the `/docs` proxy in `landing-page/server.ts`), NOT from
 * a `docs.` subdomain. Keeping docs on the same origin as the marketing site
 * consolidates link equity into one domain instead of splitting it across two.
 *
 * `SITE_URL` is overridable so preview deploys emit their own absolute URLs in
 * sitemap/canonical/OG tags rather than pointing at production.
 */
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://daysurface.com"
).replace(/\/$/, "");

/** Product name used in <title> suffixes, OG cards, and nav. */
export const SITE_NAME = "DaySurface";

/**
 * Must stay in step with `basePath` in `next.config.mjs`.
 *
 * Next prepends basePath to `<Link href>` and router navigations automatically,
 * but NOT to strings placed in metadata (canonical, hreflang, OG url) or to a
 * `sitemap.ts` entry. Those need it added by hand, which is what `absoluteUrl`
 * is for - `page.url` from the fumadocs loader is basePath-relative.
 */
export const BASE_PATH = "/docs";

/** Absolute, basePath-qualified URL for a loader-relative path. */
export function absoluteUrl(path: string): string {
  const rel = path === "/" ? "" : path.startsWith("/") ? path : `/${path}`;
  return `${SITE_URL}${BASE_PATH}${rel}`;
}
