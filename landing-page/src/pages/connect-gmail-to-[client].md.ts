import type { APIRoute, GetStaticPaths } from "astro";
import { site, clientGuides, type ClientGuide } from "../config/landing";
import { buildClientGuideMd } from "../agent/content";

/**
 * /connect-gmail-to-<client>.md - one client setup guide as markdown.
 *
 * Passes the guide through as a prop, the same way the `.astro` route does,
 * rather than handing the builder a slug to look up. `getStaticPaths` already
 * restricts the params to slugs that exist, so a lookup here could only fail in
 * a state the static build cannot produce.
 */
export const getStaticPaths = (() =>
  clientGuides.map((guide) => ({
    params: { client: guide.slug },
    props: { guide },
  }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props, site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildClientGuideMd(origin, props.guide as ClientGuide), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
