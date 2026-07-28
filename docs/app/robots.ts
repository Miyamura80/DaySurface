import type { MetadataRoute } from "next";
import { absoluteUrl } from "@/lib/site";

export const revalidate = false;

/**
 * Served at `/docs/robots.txt` because of `basePath`.
 *
 * The authoritative robots.txt for the domain is the apex one, emitted by the
 * landing page (`landing-page/src/pages/robots.txt.ts`) - crawlers only read
 * `/robots.txt` at the origin root. This exists so that the docs service, when
 * reached directly on its own Railway hostname, still advertises its sitemap
 * and points at the canonical origin rather than looking like a bare 404.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: absoluteUrl("/sitemap.xml"),
  };
}
