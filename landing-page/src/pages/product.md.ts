import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildProductMd } from "../agent/content";

/** /product.md - how DaySurface works and why it's different. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildProductMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
