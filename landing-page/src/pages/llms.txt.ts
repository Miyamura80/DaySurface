import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildLlmsTxt } from "../agent/content";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildLlmsTxt(origin), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
