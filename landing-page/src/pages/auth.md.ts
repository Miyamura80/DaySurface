import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildAuthMd } from "../agent/content";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildAuthMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
