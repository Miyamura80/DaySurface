import { isTranslated, source } from "@/lib/source";
import { i18n } from "@/lib/i18n";
import { absoluteUrl } from "@/lib/site";

export const revalidate = false;
export const dynamic = "force-static";

/**
 * Sitemap for the docs section, served at `/docs/sitemap.xml`.
 *
 * Deliberately a hand-written route rather than Next's `app/sitemap.ts`
 * convention: that convention can only emit at the origin root, and `/sitemap.xml`
 * on this domain already belongs to the landing page
 * (`landing-page/src/pages/sitemap.xml.ts`). Only `/docs`-prefixed paths are
 * proxied to this service, so a root-level sitemap would be unreachable from the
 * apex. The apex robots.txt lists both files, so one Search Console submission
 * covers the marketing pages and the docs.
 *
 * This lives outside `[lang]` because it is not localized, and it is exempted
 * from the i18n middleware matcher so it is not rewritten to
 * `/en/docs/sitemap.xml` (a path with no route). Keep those two facts together:
 * moving one without the other silently 404s the file, which is how the
 * previous `docs.daysurface.com/sitemap.xml` came to be missing.
 */
function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function GET(): Response {
  const entries: string[] = [];

  for (const locale of i18n.languages) {
    for (const page of source.getPages(locale)) {
      // Skip locales that merely fall back to English. `getPages` yields an
      // entry for every page in every locale, but only `index` is actually
      // translated - listing the rest would submit 69 localized URLs serving
      // identical English content, which is thin duplication, not reach.
      if (!isTranslated(page, locale)) continue;

      // Same page in the other locales, restricted to real translations for the
      // same reason: hreflang naming a language the page is not written in is a
      // misdeclaration.
      const alternates = i18n.languages
        .map((other) => {
          const localized = source.getPage(page.slugs, other);
          if (!localized || !isTranslated(localized, other)) return null;
          return (
            `    <xhtml:link rel="alternate" hreflang="${other}" ` +
            `href="${xmlEscape(absoluteUrl(localized.url))}" />`
          );
        })
        .filter(Boolean)
        .join("\n");

      entries.push(
        `  <url>\n` +
          `    <loc>${xmlEscape(absoluteUrl(page.url))}</loc>\n` +
          `${alternates}\n` +
          `  </url>`,
      );
    }
  }

  const body =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n` +
    `        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n` +
    `${entries.join("\n")}\n` +
    `</urlset>\n`;

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
}
