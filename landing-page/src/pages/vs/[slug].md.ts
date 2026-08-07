import type { APIRoute, GetStaticPaths } from "astro";
import { site, comparison, type Competitor } from "../../config/landing";
import { buildVsMd } from "../../agent/content";

/**
 * /vs/<slug>.md - one head-to-head comparison per competitor, as markdown.
 *
 * Passes the competitor through as a prop, matching the `.astro` route. See
 * `buildVsMd` for why the lookup it replaced could not fail.
 */
export const getStaticPaths = (() =>
  comparison.competitors.map((competitor) => ({
    params: { slug: competitor.id },
    props: { competitor },
  }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props, site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildVsMd(origin, props.competitor as Competitor), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
