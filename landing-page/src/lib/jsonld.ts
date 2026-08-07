/**
 * JSON-LD fragment builders shared by the editorial pages.
 *
 * The FAQPage and BreadcrumbList blocks were written out longhand on every page
 * that has them - three copies each, differing only in which config array they
 * mapped over. schema.org shapes are the kind of thing that gets fixed on one
 * page and silently left wrong on the others, so they live here once.
 *
 * Deliberately NOT a general "build me any schema" helper: these two shapes are
 * what the site repeats. A page needing TechArticle, HowTo or ItemList writes it
 * inline, where the properties it chooses are visible next to the content they
 * describe.
 */
import type { FaqItem } from "../config/landing";

/** One crumb. `item` is an absolute URL - Google ignores relative ones. */
export interface Crumb {
  name: string;
  item: string;
}

/**
 * FAQPage from the same array the page renders into `<details>` elements.
 *
 * Both must stay in sync: marking up an answer that is not in the served HTML is
 * exactly what Google's structured-data guidelines call out, which is why the
 * pages render `<details>` rather than a JS accordion.
 */
export function faqPageLd(items: FaqItem[]) {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.a },
    })),
  };
}

/** BreadcrumbList, positions numbered from 1 in the order given. */
export function breadcrumbLd(crumbs: Crumb[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((c, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: c.name,
      item: c.item,
    })),
  };
}
