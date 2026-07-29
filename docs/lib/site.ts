/**
 * Canonical identity for the docs site.
 *
 * The docs are served from the apex under `/docs` (the landing page reverse
 * proxies that prefix - see `landing-page/server.ts`), NOT from a `docs.`
 * subdomain. Keeping docs on the same origin as the marketing site consolidates
 * link equity into one domain instead of splitting it across two.
 *
 * Note there is deliberately no `basePath` in `next.config.mjs`. The `/docs`
 * segment comes from the loader's `baseUrl`, which keeps `page.url` a
 * browser-usable path that can be dropped into an href, a `fetch`, or a `<meta>`
 * without further processing.
 *
 * `SITE_URL` is overridable so preview deploys emit their own absolute URLs in
 * sitemap/canonical/OG tags rather than pointing at production.
 */
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://daysurface.com"
).replace(/\/$/, "");

/** Product name used in OG cards. */
export const SITE_NAME = "DaySurface";

/**
 * Absolute URL for a site-relative path such as `page.url`.
 *
 * Trailing slashes are stripped so a path and its slashed spelling can never
 * produce two canonical URLs for one page.
 */
export function absoluteUrl(path: string): string {
  const rel = path.startsWith("/") ? path : `/${path}`;
  return `${SITE_URL}${rel}`.replace(/\/+$/, "");
}
