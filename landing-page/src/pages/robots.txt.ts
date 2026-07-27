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

# NLWeb / Schema Map feed of structured (schema.org) data
Schemamap: ${origin}/schemamap.xml
# NLWeb ask endpoint: ${origin}/ask

# LLM-friendly documentation
# llms.txt: ${origin}/llms.txt
# llms-full.txt: ${origin}/llms-full.txt
`;

  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
