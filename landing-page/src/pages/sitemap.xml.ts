import type { APIRoute } from "astro";
import { site, comparison, pricing } from "../config/landing";

// Static routes that ship in dist/. Keep in sync with src/pages/*.astro.
const routes = [
  { path: "/", priority: "1.0", changefreq: "weekly" },
  // The canonical "how do I get started / is there an account" answer. High
  // priority: it is the page every onboarding query should resolve to, and the
  // one the /signup, /login and /get-started aliases canonicalise onto. Those
  // aliases are deliberately absent - they are noindex duplicates of this URL.
  { path: "/connect", priority: "0.9", changefreq: "monthly" },
  // Entry point into the docs section, which is proxied from the Next.js docs
  // service (see `server.ts`) and ships its own `/docs/sitemap.xml` covering
  // every page. Listing the index here gives crawlers a path in from the apex.
  { path: "/docs", priority: "0.9", changefreq: "weekly" },
  { path: "/compare", priority: "0.8", changefreq: "monthly" },
  // The standalone Gmail-webhooks resource. Ranked above /compare because it is
  // the page targeting an existing organic query cluster rather than one that
  // only converts traffic already on the site.
  { path: "/gmail-webhooks", priority: "0.8", changefreq: "monthly" },
  ...(pricing.enabled
    ? [{ path: "/pricing", priority: "0.9", changefreq: "monthly" }]
    : []),
  { path: "/api", priority: "0.7", changefreq: "weekly" },
  // One /vs/<slug> page per competitor (generated from the comparison config).
  ...comparison.competitors.map((c) => ({
    path: `/vs/${c.id}`,
    priority: "0.7",
    changefreq: "monthly",
  })),
  { path: "/about", priority: "0.5", changefreq: "yearly" },
  // The canonical support & contact page. /help and /contact serve the same
  // body but are deliberately absent - they are noindex aliases of this URL.
  { path: "/support", priority: "0.5", changefreq: "monthly" },
  { path: "/privacy", priority: "0.3", changefreq: "yearly" },
  { path: "/terms", priority: "0.3", changefreq: "yearly" },
];

/**
 * The form a page's own `<link rel="canonical">` uses.
 *
 * Astro builds each page as `<path>/index.html` and canonicalises to the
 * trailing-slash URL, so listing `/compare` here pointed crawlers at a
 * non-canonical variant of every page and made them lean on canonical
 * consolidation for something the sitemap could just state correctly.
 *
 * `/docs` is exempt: it is proxied to the Next.js docs service, which owns its
 * own canonical form and ships its own sitemap.
 */
function canonicalPath(path: string): string {
  if (path === "/" || path === "/docs") return path;
  return path.endsWith("/") ? path : `${path}/`;
}

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  const lastmod = new Date().toISOString().split("T")[0];

  const urls = routes
    .map(
      (r) =>
        `  <url>\n` +
        `    <loc>${origin}${canonicalPath(r.path)}</loc>\n` +
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
