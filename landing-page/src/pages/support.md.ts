import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildSupportMd } from "../agent/content";

/** /support.md - the markdown twin of the support & contact page. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildSupportMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
