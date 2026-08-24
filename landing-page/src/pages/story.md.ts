import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildStoryMd } from "../agent/content";

/** /story.md - why we built DaySurface. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildStoryMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
