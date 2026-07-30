import type { APIRoute } from "astro";
import { site } from "../config/landing";

export const GET: APIRoute = ({ site: astroSite }) => {
  const origin = (astroSite ?? new URL(site.url)).origin;

  // AI / LLM crawlers we explicitly welcome. Listing them (rather than relying
  // on the wildcard) is what AI-readiness audits look for.
  const aiAgents = [
    "GPTBot",
    "ChatGPT-User",
    "OAI-SearchBot",
    "ClaudeBot",
    "Claude-Web",
    "Claude-User",
    "anthropic-ai",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Googlebot",
    "Applebot-Extended",
    "CCBot",
    "Amazonbot",
    "Bytespider",
    "Meta-ExternalAgent",
    "cohere-ai",
    "DuckAssistBot",
    "YouBot",
  ];

  const body = `# robots.txt for ${site.name}
# AI agents and crawlers are welcome. See /llms.txt and /agents.md.

User-agent: *
Allow: /

${aiAgents.map((a) => `User-agent: ${a}\nAllow: /`).join("\n\n")}

# Sitemaps
Sitemap: ${origin}/sitemap.xml
# Docs live under /docs on this origin and ship their own sitemap (Next.js
# basePath), so both must be listed for full coverage from one submission.
Sitemap: ${origin}/docs/sitemap.xml

# NLWeb / Schema Map feed of structured (schema.org) data
Schemamap: ${origin}/schemamap.xml
# No NLWeb /ask endpoint is advertised here. The server ships one
# (api_server/routes/ask.py) but it is disabled by default and off on the hosted
# service, and this apex never served it at all - the line that used to point
# here sent agents to the SPA fallback, i.e. 272KB of homepage at a 200. Restore
# it only once /ask actually answers on this origin.

# LLM-friendly documentation
# llms.txt: ${origin}/llms.txt
# llms-full.txt: ${origin}/llms-full.txt
`;

  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
