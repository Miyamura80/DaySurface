import type { MetadataRoute } from "next";
import { source } from "@/lib/source";
import { i18n } from "@/lib/i18n";
import { absoluteUrl } from "@/lib/site";

// Next only emits this at the app root, so the file lives outside `[lang]` and
// enumerates every locale itself.
export const revalidate = false;

/**
 * Sitemap for the docs section.
 *
 * `docs.daysurface.com/sitemap.xml` used to 404, which left all ~27 pages
 * undiscoverable except by crawling links. This lists every page in every
 * locale with `alternates.languages`, the sitemap-level equivalent of the
 * hreflang tags emitted in `generateMetadata`.
 *
 * Served at `/docs/sitemap.xml` and referenced from the apex `/sitemap.xml`
 * (see `landing-page/src/pages/sitemap.xml.ts`), so one submission in Search
 * Console reaches both the marketing pages and the docs.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];

  for (const locale of i18n.languages) {
    for (const page of source.getPages(locale)) {
      // Same page in the other locales, for the <xhtml:link> alternates.
      const languages: Record<string, string> = {};
      for (const other of i18n.languages) {
        const localized = source.getPage(page.slugs, other);
        if (localized) languages[other] = absoluteUrl(localized.url);
      }

      entries.push({
        url: absoluteUrl(page.url),
        changeFrequency: "weekly",
        // The docs index is the section entry point; everything else is a leaf.
        priority: page.slugs.length === 0 ? 0.9 : 0.7,
        alternates: { languages },
      });
    }
  }

  return entries;
}
