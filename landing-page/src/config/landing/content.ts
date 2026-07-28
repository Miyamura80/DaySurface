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
  subhead: "Have your assistant explain what it does, how it compares, and how to connect it.",
  prompt: `What is ${site.name}? It is an MCP server for Gmail that lets an AI assistant triage a ranked inbox, draft replies in a real composer, and fill and sign PDF attachments - all inside Claude, ChatGPT, or any MCP client. Explain what it can do, how it compares to other Gmail MCP servers, and how I would connect it. Site: ${site.url} - source: ${site.githubUrl}`,
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
      q: "What can it actually do with my email?",
      a: "Rank your inbox by what needs attention, search and read threads, draft and send replies in a real composer, manage labels, archive, handle attachments, and fill and sign PDF attachments without leaving the chat. 34 tools in total, all through the official Gmail API.",
    },
    {
      q: "Can it send email without me?",
      a: "The tools are built so replies land in a composer you review and edit first - your agent writes, you press send. Signing is stronger still: the assistant can fill a PDF's fields, but the signature itself is a step only you can complete by typing your own name. It cannot sign on your behalf.",
    },
    {
      q: "Do you read or train on my email?",
      a: "No. Your email is never sent to an AI model by us - DaySurface runs no LLM inference on Gmail content, and the inbox ranking is deterministic code, not a model. Mail goes only to the AI client you chose and connected, under that client's own terms. Access uses Google's gmail.modify scope; see the privacy policy for the full detail.",
    },
    {
      q: "Do I need to install anything to use it?",
      a: "No. The server runs over streamable HTTP, so connecting is just adding its URL to your client - one click in Claude, Cursor, VS Code, and Goose. No local install, runtime, or download. (Self-hosting is a separate, optional step.)",
    },
    {
      q: "Which clients does it work with?",
      a: "Anything that speaks the Model Context Protocol: Claude, ChatGPT, Claude Code, Cursor, VS Code, Goose, Cline, Zed, Windsurf, and more. Pick yours in the connect box above - some get a one-click install link, the rest take a pasted URL or a setup prompt.",
    },
    {
      q: "Does it work on mobile?",
      a: "Yes, anywhere your agent runs. It's a remote server with nothing to install locally, so it works in any agent app with a mobile client, including the Claude and ChatGPT mobile apps.",
    },
    {
      q: "Can I self-host?",
      a: "Yes. The whole thing is open source and ships with a Dockerfile and Railway config, so you can run it on your own infrastructure with your own Google OAuth credentials. Deploy it anywhere that runs a container.",
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
  heading: "Put your inbox in your chat.",
  subhead: "Pick your client, add the server, and your agent can start triaging today.",
  // Four flagship features, 3–4 words each, shown beside the final CTA.
  // End-user outcomes, not architecture - the transports story lives on /api.
  features: [
    "Ranked inbox triage",
    "Drafts you edit, not approve",
    "Fill and sign PDFs in-thread",
    "Open source, self-hostable",
  ],
};
