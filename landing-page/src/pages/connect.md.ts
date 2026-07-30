import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildConnectMd } from "../agent/content";

/**
 * /connect.md - the connect page as markdown.
 *
 * Also what `Accept: text/markdown` on /connect and on every intent alias
 * returns (see server.ts), and the body a fetcher gets without 250KB of
 * Tailwind markup around it.
 */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildConnectMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
