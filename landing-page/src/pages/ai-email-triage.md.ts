import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildTriageMd } from "../agent/content";

/** /ai-email-triage.md - the triage argument, table and FAQ inlined. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildTriageMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
