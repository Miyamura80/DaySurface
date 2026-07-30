import type { APIRoute } from "astro";
import { site, comparison, pricing } from "../config/landing";
import { ROUTE_SOURCES, VS_SOURCES, lastmodTag } from "../lib/lastmod";

/**
 * NLWeb / Schema Map feed (referenced via the `Schemamap:` directive in
 * robots.txt). It points crawlers at the URLs on this site that embed
 * schema.org structured data (JSON-LD), declaring the type and format of each.
 * See https://schemamap.org.
 */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;

  // `sources` feeds the per-URL <lastmod>; see src/lib/lastmod.ts for why these
  // are git dates rather than the build clock.
  const feeds: { path: string; type: string; sources: readonly string[] }[] = [
    { path: "/", type: "SoftwareApplication", sources: ROUTE_SOURCES["/"]! },
    { path: "/", type: "Organization", sources: ROUTE_SOURCES["/"]! },
    { path: "/", type: "FAQPage", sources: ROUTE_SOURCES["/"]! },
    // /gmail-webhooks embeds WebPage + FAQPage + BreadcrumbList JSON-LD.
    {
      path: "/gmail-webhooks",
      type: "WebPage",
      sources: ROUTE_SOURCES["/gmail-webhooks"]!,
    },
    {
      path: "/gmail-webhooks",
      type: "FAQPage",
      sources: ROUTE_SOURCES["/gmail-webhooks"]!,
    },
    {
      path: "/gmail-webhooks",
      type: "BreadcrumbList",
      sources: ROUTE_SOURCES["/gmail-webhooks"]!,
    },
    // /compare embeds WebPage + ItemList + BreadcrumbList JSON-LD.
    { path: "/compare", type: "WebPage", sources: ROUTE_SOURCES["/compare"]! },
    { path: "/compare", type: "ItemList", sources: ROUTE_SOURCES["/compare"]! },
    { path: "/compare", type: "BreadcrumbList", sources: ROUTE_SOURCES["/compare"]! },
    // /pricing embeds Product (with an Offer per tier) + FAQPage + BreadcrumbList.
    ...(pricing.enabled
      ? [
          { path: "/pricing", type: "Product", sources: ROUTE_SOURCES["/pricing"]! },
          { path: "/pricing", type: "FAQPage", sources: ROUTE_SOURCES["/pricing"]! },
          {
            path: "/pricing",
            type: "BreadcrumbList",
            sources: ROUTE_SOURCES["/pricing"]!,
          },
        ]
      : []),
    // Each /vs/<slug> page embeds WebPage + BreadcrumbList JSON-LD.
    ...comparison.competitors.flatMap((c) => [
      { path: `/vs/${c.id}`, type: "WebPage", sources: VS_SOURCES },
      { path: `/vs/${c.id}`, type: "BreadcrumbList", sources: VS_SOURCES },
    ]),
  ];

  const entries = feeds
    .map(
      (f) =>
        `  <url>\n` +
        `    <loc>${origin}${f.path}</loc>\n` +
        lastmodTag(f.sources) +
        `    <schemamap:schema>\n` +
        `      <schemamap:type>${f.type}</schemamap:type>\n` +
        `      <schemamap:format>application/ld+json</schemamap:format>\n` +
        `      <schemamap:embedding>jsonld</schemamap:embedding>\n` +
        `    </schemamap:schema>\n` +
        `  </url>`,
    )
    .join("\n");

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:schemamap="https://schemamap.org/ns/0.1">
${entries}
</urlset>
`;

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
