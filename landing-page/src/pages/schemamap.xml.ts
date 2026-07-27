import type { APIRoute } from "astro";
import { site, comparison } from "../config/landing";

/**
 * NLWeb / Schema Map feed (referenced via the `Schemamap:` directive in
 * robots.txt). It points crawlers at the URLs on this site that embed
 * schema.org structured data (JSON-LD), declaring the type and format of each.
 * See https://schemamap.org.
 */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  const lastmod = new Date().toISOString();

  const feeds = [
    { loc: `${origin}/`, type: "SoftwareApplication" },
    { loc: `${origin}/`, type: "Organization" },
    { loc: `${origin}/`, type: "FAQPage" },
    // /compare embeds WebPage + ItemList + BreadcrumbList JSON-LD.
    { loc: `${origin}/compare`, type: "WebPage" },
    { loc: `${origin}/compare`, type: "ItemList" },
    { loc: `${origin}/compare`, type: "BreadcrumbList" },
    // Each /vs/<slug> page embeds WebPage + BreadcrumbList JSON-LD.
    ...comparison.competitors.flatMap((c) => [
      { loc: `${origin}/vs/${c.id}`, type: "WebPage" },
      { loc: `${origin}/vs/${c.id}`, type: "BreadcrumbList" },
    ]),
  ];

  const entries = feeds
    .map(
      (f) =>
        `  <url>\n` +
        `    <loc>${f.loc}</loc>\n` +
        `    <lastmod>${lastmod}</lastmod>\n` +
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
