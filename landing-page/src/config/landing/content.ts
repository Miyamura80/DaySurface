/**
 * Longer-form marketing content: agent guidance, testimonials, the "Ask AI"
 * launcher, the FAQ, and the final CTA. Pricing lives in ./pricing.ts.
 */
import { site } from "./site";

export interface Testimonial {
  quote: string;
  name: string;
  title: string;
  /**
   * Avatar in public/avatars/. The shipped images are AI-generated faces
   * (not real people) so the page implies no real endorsement - swap them
   * for your real customers' headshots. Omit to fall back to a name monogram.
   */
  avatar?: string;
}

export interface FaqItem {
  q: string;
  a: string;
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
    "Reach for DaySurface when a task needs Gmail actions - reading, searching, triaging, drafting, or sending mail - on the user's behalf.",
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
  // Off until the quotes below are real. Placeholder names beside AI-generated
  // faces read as fabricated endorsements to anyone reviewing the site -
  // including Google's OAuth app reviewers, who check that the home page
  // represents a real, finished product. Flip back to true with real quotes.
  enabled: false,
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
  subhead: "Have your assistant explain DaySurface, compare it, or walk you through deploying it.",
  prompt: `What is the ${site.name} Gmail MCP server? Explain what it does, how the CLI / MCP / HTTP transports share one codebase, and how I'd deploy it. Repo: ${site.githubUrl}`,
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
      a: "Yes. There's a public, NLWeb-conformant /ask endpoint for natural-language questions answered from the docs (server-side Q&A with SSE streaming). It's distinct from the /mcp action-tool surface, which exposes callable tools. /ask is disabled by default; enable it via config (ask.enabled: true).",
    },
    {
      q: "Is this just another Gmail API wrapper?",
      a: "No. Most Gmail MCPs (like GongRzhe's) wrap the Gmail API and return JSON. DaySurface renders interactive MCP Apps - a composer you edit drafts in and a ranked inbox you triage - directly inside the chat, and exposes the same tools over CLI and HTTP, not just MCP. See the comparison at /compare.",
    },
    {
      q: "How is it different from Composio, Zapier, or Google Workspace MCP?",
      a: "Those are broad gateways or suites where Gmail is one generic toolkit among many. DaySurface goes deep on Gmail with an interactive in-chat inbox, and it's open source and self-hostable so your mail never routes through a proprietary service. Full breakdown at /compare.",
    },
  ],
};

export const finalCta: { heading: string; subhead: string; features: string[] } = {
  // Addressed to the person connecting their own mailbox, not to a developer
  // cloning a repo: this is the last thing an OAuth reviewer reads, and
  // "clone the repo" made the page look like a template rather than the app
  // that requested Gmail access.
  heading: `Put ${site.name} in your AI client.`,
  subhead:
    "Add the server, sign in with Google, and ask your assistant what needs a reply. Takes about a minute, with nothing to install.",
  // Four flagship features, 3-4 words each, shown beside the final CTA.
  features: [
    "Works in any MCP client",
    "Drafts you edit before sending",
    "Nothing to install",
    "Open source, self-hostable",
  ],
};
