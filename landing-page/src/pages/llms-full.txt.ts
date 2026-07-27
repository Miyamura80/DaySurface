import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildLlmsFullTxt } from "../agent/content";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildLlmsFullTxt(origin), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
