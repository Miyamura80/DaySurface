import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildSupportMd } from "../agent/content";

/**
 * /contact.md - the markdown twin of the /contact alias, identical to
 * /support.md. Same rationale as help.md.ts: keeps /contact markdown-negotiable
 * and directly fetchable, marked noindex by the server.
 */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildSupportMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
