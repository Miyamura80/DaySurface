import type { APIRoute, GetStaticPaths } from "astro";
import { site, connectAliases } from "../config/landing";
import { buildConnectMd } from "../agent/content";

/**
 * Markdown twins for the intent aliases: /signup.md, /login.md, and so on.
 *
 * An agent that guesses `/signup` often guesses `/signup.md` next once it has
 * learned the site publishes markdown twins. Cheap to emit, and it removes the
 * one remaining way to probe the connect answer and get a 404.
 */
export const getStaticPaths = (() =>
  connectAliases.map((intent) => ({ params: { intent } }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildConnectMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
