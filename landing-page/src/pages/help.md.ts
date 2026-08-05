import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildSupportMd } from "../agent/content";

/**
 * /help.md - the markdown twin of the /help alias, identical to /support.md.
 * Exists so `Accept: text/markdown` on /help (and a direct /help.md fetch)
 * returns the support body, matching the /connect intent-alias pattern. Marked
 * noindex by the server (see supportAliases in server.ts) so it is not an
 * indexable duplicate of /support.md.
 */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildSupportMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
