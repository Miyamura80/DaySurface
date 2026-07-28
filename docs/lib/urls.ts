import { i18n } from "@/lib/i18n";

/**
 * Internal docs link builder, mirroring `hideLocale: "default-locale"`.
 *
 * With that setting the default locale carries no prefix, so English pages are
 * at `/<slug>` and translations at `/<locale>/<slug>` - both relative to the
 * `/docs` basePath, which Next prepends when the string reaches a `<Link>`.
 * Hand-written links must follow the same rule or the i18n middleware bounces
 * the user through a redirect, which wastes crawl budget.
 *
 * For absolute URLs in metadata or the sitemap use `absoluteUrl` instead -
 * metadata is not basePath-aware.
 *
 * @param slug page path relative to the docs root, e.g. `"mcp/setup"` or `""`
 */
export function docsPath(slug = "", locale: string = i18n.defaultLanguage): string {
  const prefix = locale === i18n.defaultLanguage ? "" : `/${locale}`;
  const tail = slug.replace(/^\/+/, "");
  return `${prefix}/${tail}`.replace(/\/$/, "") || "/";
}
