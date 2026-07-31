/**
 * schema.org JSON-LD for docs pages.
 *
 * The marketing site has embedded structured data since launch
 * (`landing-page/src/pages/*.astro`); the docs had none, so every docs URL was
 * an untyped `WebPage` to crawlers and answer engines. This module closes that
 * gap with three graph nodes per page:
 *
 * - `TechArticle` - types the page as developer documentation rather than
 *   generic prose, and carries the headline/description/canonical.
 * - `BreadcrumbList` - drives the breadcrumb trail Google renders in place of
 *   the raw URL in results. This is the node with live rich-result support.
 *
 * Deliberately no `FAQPage` here. The Q&A for the Gmail-webhooks cluster lives
 * on the marketing site's `/gmail-webhooks` page, which owns that search intent;
 * emitting the same questions from a docs URL on the same origin would put two
 * pages on one query. If a docs page ever needs its own FAQ, it needs its own
 * questions too.
 */
import { SITE_NAME, absoluteUrl } from "@/lib/site";

interface BuildArgs {
  url: string;
  title: string;
  description?: string;
  imageUrl: string;
  /** Sidebar trail above this page, e.g. `["Documentation", "MCP"]`. */
  breadcrumbs: { name: string; url: string }[];
}

export function buildDocsJsonLd({
  url,
  title,
  description,
  imageUrl,
  breadcrumbs,
}: BuildArgs): Record<string, unknown> {
  const canonical = absoluteUrl(url);

  const graph: Record<string, unknown>[] = [
    {
      "@type": "TechArticle",
      "@id": `${canonical}#article`,
      headline: title,
      ...(description ? { description } : {}),
      image: absoluteUrl(imageUrl),
      inLanguage: "en",
      isPartOf: {
        "@type": "WebSite",
        name: `${SITE_NAME} docs`,
        url: absoluteUrl("/docs"),
      },
      publisher: {
        "@type": "Organization",
        name: SITE_NAME,
        url: absoluteUrl("/"),
      },
      mainEntityOfPage: { "@type": "WebPage", "@id": canonical },
    },
    {
      "@type": "BreadcrumbList",
      "@id": `${canonical}#breadcrumbs`,
      // Ancestors pointing at this page are dropped before the page itself is
      // appended: a folder's own landing page (`/docs/mcp`) is both the last
      // ancestor and the current page, which would otherwise emit the same URL
      // at two positions.
      itemListElement: [
        ...breadcrumbs.filter((crumb) => crumb.url !== url),
        { name: title, url },
      ].map((crumb, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: crumb.name,
        item: absoluteUrl(crumb.url),
      })),
    },
  ];

  return { "@context": "https://schema.org", "@graph": graph };
}
