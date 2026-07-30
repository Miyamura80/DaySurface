import type { APIRoute } from "astro";
import { site, comparison, pricing } from "../config/landing";
import { ROUTE_SOURCES, VS_SOURCES, lastmodTag } from "../lib/lastmod";

/** `sources` defaults to the ROUTE_SOURCES entry for `path` when omitted. */
type Route = {
  path: string;
  priority: string;
  changefreq: string;
  sources?: readonly string[];
};

// Static routes that ship in dist/. Keep in sync with src/pages/*.astro.
const routes: Route[] = [
  { path: "/", priority: "1.0", changefreq: "weekly" },
  // Entry point into the docs section, which is proxied from the Next.js docs
  // service (see `server.ts`) and ships its own `/docs/sitemap.xml` covering
  // every page. Listing the index here gives crawlers a path in from the apex.
  { path: "/docs", priority: "0.9", changefreq: "weekly" },
  { path: "/compare", priority: "0.8", changefreq: "monthly" },
  ...(pricing.enabled
    ? [{ path: "/pricing", priority: "0.9", changefreq: "monthly" }]
    : []),
  { path: "/api", priority: "0.7", changefreq: "weekly" },
  // One /vs/<slug> page per competitor (generated from the comparison config).
  ...comparison.competitors.map((c) => ({
    path: `/vs/${c.id}`,
    priority: "0.7",
    changefreq: "monthly",
    sources: VS_SOURCES,
  })),
  { path: "/privacy", priority: "0.3", changefreq: "yearly" },
  { path: "/terms", priority: "0.3", changefreq: "yearly" },
];

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;

  const urls = routes
    .map(
      (r) =>
        `  <url>\n` +
        `    <loc>${origin}${r.path}</loc>\n` +
        lastmodTag(r.sources ?? ROUTE_SOURCES[r.path] ?? []) +
        `    <changefreq>${r.changefreq}</changefreq>\n` +
        `    <priority>${r.priority}</priority>\n` +
        `  </url>`,
    )
    .join("\n");

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>
`;

  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
