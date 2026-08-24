import type { APIRoute } from "astro";
import { site, comparison, pricing, clientGuides, guidePath } from "../config/landing";

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
  // The product page: how it works and why it's better. Ranked with /compare -
  // it is the page that turns "what is this" intent into a connect, one step
  // upstream of the alternatives query /compare targets.
  { path: "/product", priority: "0.8", changefreq: "monthly" },
  { path: "/compare", priority: "0.8", changefreq: "monthly" },
  // The standalone Gmail-webhooks resource. Ranked above /compare because it is
  // the page targeting an existing organic query cluster rather than one that
  // only converts traffic already on the site.
  { path: "/gmail-webhooks", priority: "0.8", changefreq: "monthly" },
  // One setup guide per chat client, generated from the same config the route
  // renders from so a new client cannot ship without a sitemap entry. Ranked
  // with /gmail-webhooks: same job, an existing query cluster rather than
  // traffic already on the site.
  ...clientGuides.map((g) => ({
    path: guidePath(g.slug),
    priority: "0.8",
    changefreq: "monthly",
  })),
  // The triage argument. A notch below the client guides: same intent to capture
  // demand, but a broader query with less buying intent behind it.
  { path: "/ai-email-triage", priority: "0.7", changefreq: "monthly" },
  // Listed for crawlers only when pricing is surfaced. An unlisted-but-live
  // /pricing page (enabled && !listed) is reachable by direct URL but kept out
  // of the sitemap so it is not advertised for indexing.
  ...(pricing.enabled && pricing.listed
    ? [{ path: "/pricing", priority: "0.9", changefreq: "monthly" }]
    : []),
  { path: "/api", priority: "0.7", changefreq: "weekly" },
  // One /vs/<slug> page per competitor (generated from the comparison config).
  ...comparison.competitors.map((c) => ({
    path: `/vs/${c.id}`,
    priority: "0.7",
    changefreq: "monthly",
  })),
  // The founding narrative (why we built it). Distinct from /about, which is the
  // OAuth-facing "what it does with your Gmail" page.
  { path: "/story", priority: "0.5", changefreq: "yearly" },
  { path: "/about", priority: "0.5", changefreq: "yearly" },
  // /waitlist is intentionally absent: it's noindex (see waitlist.astro), like
  // the /signup, /help and /contact aliases below. It's reachable from the
  // footer, which is the inbound path a conversion surface actually wants.
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
