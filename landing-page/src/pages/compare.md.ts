import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildCompareMd } from "../agent/content";

/** /compare.md - the comparison hub, with the capability matrix spelled out. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildCompareMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
