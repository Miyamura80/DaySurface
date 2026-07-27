import type { APIRoute } from "astro";
import { site, comparison } from "../config/landing";

// Static routes that ship in dist/. Keep in sync with src/pages/*.astro.
const routes = [
  { path: "/", priority: "1.0", changefreq: "weekly" },
  { path: "/compare", priority: "0.8", changefreq: "monthly" },
  { path: "/api", priority: "0.7", changefreq: "weekly" },
  // One /vs/<slug> page per competitor (generated from the comparison config).
  ...comparison.competitors.map((c) => ({
    path: `/vs/${c.id}`,
    priority: "0.7",
    changefreq: "monthly",
  })),
  { path: "/privacy", priority: "0.3", changefreq: "yearly" },
  { path: "/terms", priority: "0.3", changefreq: "yearly" },
];

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  const lastmod = new Date().toISOString().split("T")[0];

  const urls = routes
    .map(
      (r) =>
        `  <url>\n` +
        `    <loc>${origin}${r.path}</loc>\n` +
        `    <lastmod>${lastmod}</lastmod>\n` +
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
