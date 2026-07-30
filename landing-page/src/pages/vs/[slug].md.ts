import type { APIRoute, GetStaticPaths } from "astro";
import { site, comparison } from "../../config/landing";
import { buildVsMd } from "../../agent/content";

/** /vs/<slug>.md - one head-to-head comparison per competitor, as markdown. */
export const getStaticPaths = (() =>
  comparison.competitors.map((c) => ({ params: { slug: c.id } }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ params, site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildVsMd(origin, params.slug ?? ""), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
