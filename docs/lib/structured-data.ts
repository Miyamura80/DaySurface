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
 * - `FAQPage` - emitted only for pages with entries in `lib/faq.ts`. Google
 *   restricted FAQ *rich results* to health and government sites in 2023, so
 *   treat this as machine-readable Q&A for answer engines and LLM crawlers,
 *   not as a route to SERP accordions.
 *
 * Answers come from `lib/faq.ts`, the same source the visible `<Faq />` block
 * renders, because a `FAQPage` answer that is not present in the page body is a
 * structured-data violation.
 */
import { SITE_NAME, absoluteUrl } from "@/lib/site";
import { getFaq } from "@/lib/faq";

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

  const faq = getFaq(url);
  if (faq && faq.length > 0) {
    graph.push({
      "@type": "FAQPage",
      "@id": `${canonical}#faq`,
      mainEntity: faq.map((entry) => ({
        "@type": "Question",
        name: entry.question,
        acceptedAnswer: { "@type": "Answer", text: entry.answer },
      })),
    });
  }

  return { "@context": "https://schema.org", "@graph": graph };
}
