import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildSkillsSh } from "../agent/content";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildSkillsSh(origin), {
    headers: { "Content-Type": "text/x-shellscript; charset=utf-8" },
  });
};
