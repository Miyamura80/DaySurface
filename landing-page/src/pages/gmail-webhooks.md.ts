import type { APIRoute } from "astro";
import { site } from "../config/landing";
import { buildGmailWebhooksMd } from "../agent/content";

/** /gmail-webhooks.md - the Gmail webhooks answer, comparison and FAQ inlined. */
export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;
  return new Response(buildGmailWebhooksMd(origin), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
