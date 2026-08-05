import type { APIRoute, GetStaticPaths } from "astro";
import { site, clientGuides } from "../config/landing";
import { buildClientGuideMd } from "../agent/content";

/** /connect-gmail-to-<client>.md - one client setup guide as markdown. */
export const getStaticPaths = (() =>
  clientGuides.map((g) => ({ params: { client: g.slug } }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ params, site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildClientGuideMd(origin, params.client ?? ""), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
