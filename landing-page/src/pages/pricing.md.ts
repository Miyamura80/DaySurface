import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildPricingMd } from "../agent/content";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildPricingMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
