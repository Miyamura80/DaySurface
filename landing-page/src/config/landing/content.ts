/**
 * Longer-form marketing content: agent guidance, testimonials, pricing, the
 * "Ask AI" launcher, the FAQ, and the final CTA.
 */
import { site } from "./site";

export interface Testimonial {
  quote: string;
  name: string;
  title: string;
  /**
   * Avatar in public/avatars/. The shipped images are AI-generated faces
   * (not real people) so the template implies no real endorsement - swap them
   * for your real customers' headshots. Omit to fall back to a name monogram.
   */
  avatar?: string;
}

export interface FaqItem {
  q: string;
  a: string;
}

export interface PricingTier {
  name: string;
  price: string;
  cadence?: string;
  description: string;
  features: string[];
  cta: string;
  href: string;
  featured?: boolean;
}

/**
 * "When to use" guidance for agents - source of truth for the sections of the
 * same name in agents.md, llms.txt, and llms-full.txt (see src/agent/content.ts).
 * Phrase each entry as a trigger an agent can match against a user request.
 */
export const agentGuide: {
  summary: string;
  whenToUse: string[];
  whenNotToUse: string[];
} = {
  summary:
    "Reach for GmailMCP when a task needs Gmail actions - reading, searching, triaging, drafting, or sending mail - on the user's behalf.",
  // TODO: situations where an agent SHOULD call these tools.
  whenToUse: [
    "The user asks to read, search, or summarize their email (e.g. \"what did Sarah send about the contract?\").",
    "The user asks to triage or prioritize their inbox - call `gmail_curate_inbox` to rank threads by importance.",
    "The user asks to draft, reply to, or send a message - draft first and let the user review before sending.",
    "Another task needs a fact that lives in the user's mail (an invoice total, a confirmation number, a meeting time).",
  ],
  // TODO: situations where an agent should NOT use these tools (avoid over-triggering).
  whenNotToUse: [
    "The request is about a different mail provider (Outlook, Proton) - these tools are Gmail-only.",
    "The user has not connected an account or granted access - complete the auth flow (see auth.md) first.",
    "The task is purely local or computational and needs no access to the user's mailbox.",
  ],
};

export const testimonials: { enabled: boolean; heading: string; items: Testimonial[] } = {
  enabled: true,
  heading: "Trusted by builders",
  items: [
    {
      // TODO: replace with real quotes - even one line from a first user beats nothing.
      quote: "We had a production MCP server in front of our agents the same afternoon. The shared registry meant our CLI and API just worked too.",
      name: "Placeholder Name",
      title: "Staff Engineer, Placeholder Co.",
      avatar: "/avatars/person-1.jpg",
    },
    {
      quote: "The typed schemas are the killer feature. Our agent stopped guessing argument shapes overnight.",
      name: "Placeholder Name",
      title: "Founder, Placeholder AI",
      avatar: "/avatars/person-2.jpg",
    },
  ],
};

export const pricing: { enabled: boolean; heading: string; subhead: string; tiers: PricingTier[] } = {
  // Surfaced on the homepage AND in the machine-readable /pricing.md manifest.
  // Flip to false to hide the on-page section (the manifest still generates).
  enabled: true,
  heading: "Pricing & licensing",
  subhead:
    "Open source under the MIT license and free to self-host - no setup fee, no seat minimum. Pay only when you want us to run and scale it for you.",
  tiers: [
    {
      name: "Open Source",
      price: "$0",
      cadence: "forever",
      description:
        "MIT-licensed. Self-host the full server on your own infrastructure - zero setup cost, no license fee.",
      features: [
        "MIT license - fork, modify, and ship freely",
        "All three transports: CLI, MCP, HTTP API",
        "Interactive MCP Apps (composer + ranked inbox)",
        "Your own OAuth credentials & encrypted tokens",
        "Community support",
      ],
      cta: "Get the source",
      href: site.githubUrl,
    },
    {
      name: "Hosted Pro",
      price: "$20",
      cadence: "/mo",
      description:
        "We run the streamable-HTTP server for you. No infrastructure to manage, paste-a-URL setup.",
      features: [
        "Managed cloud deployment (zero ops)",
        "Hosted OAuth 2.1 & encrypted token storage",
        "Usage analytics & monitoring",
        "Priority support",
      ],
      cta: "Start free trial",
      href: "/#how-it-works",
      featured: true,
    },
    {
      name: "Team",
      price: "Custom",
      description: "For teams running agents in production, with commercial licensing options.",
      features: [
        "SSO + audit logs",
        "Commercial / OEM licensing",
        "Uptime SLA",
        "Dedicated support & onboarding",
      ],
      cta: "Contact sales",
      href: "/#how-it-works",
    },
  ],
};

/**
 * "Ask AI about this" - links that open an assistant with a pre-filled prompt
 * about the project. Each provider URL has a `{q}` placeholder; AskAi.astro
 * substitutes the encoded prompt at build time.
 */
export interface AskAiProvider {
  id: "chatgpt" | "perplexity" | "claude";
  name: string;
  logo: string;
  url: string;
}

export const askAi: {
  heading: string;
  subhead: string;
  prompt: string;
  providers: AskAiProvider[];
} = {
  heading: "Ask AI about this",
  subhead: "Have your assistant explain the template, compare it, or walk you through deploying it.",
  prompt: `What is the ${site.name} MCP server template? Explain what it does, how the CLI / MCP / HTTP transports share one codebase, and how I'd deploy it. Repo: ${site.githubUrl}`,
  providers: [
    { id: "chatgpt", name: "ChatGPT", logo: "/logos/chatgpt.svg", url: "https://chatgpt.com/?q={q}" },
    { id: "perplexity", name: "Perplexity", logo: "/logos/perplexity.svg", url: "https://www.perplexity.ai/search?q={q}" },
    { id: "claude", name: "Claude", logo: "/logos/claude.svg", url: "https://claude.ai/new?q={q}" },
  ],
};

export const faq: { heading: string; items: FaqItem[] } = {
  heading: "Frequently asked questions",
  items: [
    {
      q: "Which MCP clients are supported?",
      a: "Any client that speaks the Model Context Protocol: Claude Desktop, Claude Code, Cursor, Cline, VS Code, Windsurf, and more. The server exposes a standard tool/resource surface.",
    },
    {
      q: "stdio or streamable HTTP?",
      a: "Both. Streamable HTTP is the primary transport (mounted at /mcp alongside the HTTP API in one process), and stdio is available for local/dev use.",
    },
    {
      q: "How does authentication work?",
      a: "The MCP mount supports OAuth 2.1 as a resource server, sharing auth and CORS with the HTTP API. You can also run it unauthenticated for local development.",
    },
    {
      q: "Do I need to install anything to use it?",
      a: "No. Because the server runs over streamable HTTP, connecting is just pasting its URL into your agent client. No local install, runtime, or download required. (Self-hosting the server is a separate, optional step.)",
    },
    {
      q: "Does it work on mobile?",
      a: "Yes, anywhere your agent runs. Since it's a remote HTTP server with nothing to install locally, it works in any agent app that has a mobile app, including the Claude and ChatGPT mobile apps.",
    },
    {
      q: "Can I self-host?",
      a: "Yes. The whole thing is open source and ships with a Dockerfile and Railway config. Deploy it anywhere that runs a container.",
    },
    {
      q: "What about my existing CLI / API?",
      a: "They share the same service registry. Add a tool once and it's available over CLI, MCP, and HTTP simultaneously, with no duplicated logic.",
    },
    {
      q: "Is there an /ask (NLWeb) endpoint?",
      a: "Yes. There's a public, NLWeb-conformant /ask endpoint for natural-language questions answered from the docs (server-side Q&A with SSE streaming). It's distinct from the /mcp action-tool surface, which exposes callable tools. /ask is disabled by default in the template; enable it via config (ask.enabled: true).",
    },
    {
      q: "Is this just another Gmail API wrapper?",
      a: "No. Most Gmail MCPs (like GongRzhe's) wrap the Gmail API and return JSON. GmailMCP renders interactive MCP Apps - a composer you edit drafts in and a ranked inbox you triage - directly inside the chat, and exposes the same tools over CLI and HTTP, not just MCP. See the comparison at /compare.",
    },
    {
      q: "How is it different from Composio, Zapier, or Google Workspace MCP?",
      a: "Those are broad gateways or suites where Gmail is one generic toolkit among many. GmailMCP goes deep on Gmail with an interactive in-chat inbox, and it's open source and self-hostable so your mail never routes through a proprietary service. Full breakdown at /compare.",
    },
  ],
};

export const finalCta: { heading: string; subhead: string; features: string[] } = {
  heading: "Ship your MCP server today.",
  subhead: "Clone the template, deploy it, and point your agent at the URL.",
  // Four flagship features, 3–4 words each, shown beside the final CTA.
  features: [
    "Three transports, one codebase",
    "Headless or interactive tools",
    "Streamable HTTP, one port",
    "Open source, self-hostable",
  ],
};
