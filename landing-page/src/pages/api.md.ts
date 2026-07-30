import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildApiMd } from "../agent/content";

/** /api.md - the API page as markdown; the HTML one renders in client JS. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildApiMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
