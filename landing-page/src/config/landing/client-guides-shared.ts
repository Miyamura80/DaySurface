/**
 * Types and shared copy for the `/connect-gmail-to-<client>` guides.
 *
 * Split out of `client-guides.ts` so the contract and the reusable copy sit
 * apart from the guide data, which is the part that grows every time a client is
 * added. `client-guides.ts` was within ~50 lines of the 500-line file limit with
 * five guides in it. Nothing here imports the data module.
 */
import type { FaqItem } from "./content";

/** One numbered setup step. */
export interface GuideStep {
  title: string;
  body: string;
}

/** Something the reader can do once connected, phrased as an outcome. */
export interface GuideCapability {
  title: string;
  body: string;
}

export interface ClientGuide {
  /** URL segment: `/connect-gmail-to-<slug>`. */
  slug: string;
  /**
   * Matching `connect.ts` target id, so the two can be cross-checked.
   *
   * `null` for a host the install picker deliberately does not offer. Today
   * that is only Microsoft 365 Copilot: a tenant admin provisions it from the
   * Microsoft 365 admin center, so listing it in a self-serve widget would
   * promise the visitor something they cannot do. A guide with a null target
   * has to say who does the work in its own copy - this one leads with it.
   */
  targetId: string | null;
  clientName: string;
  /** <title>. Leads with the query, closes with the brand. */
  title: string;
  description: string;
  heading: string;
  subhead: string;
  lede: string;
  /** Anything that must be true before step 1 works. Empty renders nothing. */
  prerequisites: string[];
  steps: GuideStep[];
  capabilities: GuideCapability[];
  faq: FaqItem[];
}

/**
 * The end-user capability list, and ONLY valid for hosts that render MCP Apps.
 *
 * The first two entries describe an iframe surface - a ranked inbox dashboard
 * and an editable composer - delivered through the MCP Apps extension (`ui://`
 * resources, see mcp_server/MCP_UI_ARCHITECTURE.md). A host that speaks MCP but
 * does not implement Apps gets tool calls and JSON, not a dashboard, so putting
 * this list on its page would describe something the reader will never see.
 *
 * Apps hosts as of August 2026, per the official extension support matrix at
 * modelcontextprotocol.io/extensions/client-matrix: Claude (web), Claude
 * Desktop, ChatGPT, VS Code Copilot, Microsoft 365 Copilot, Goose, Postman,
 * MCPJam, Cursor, Archestra.AI, PostHog Code.
 *
 * NOT Apps hosts, despite having install targets or logos on this site: Codex
 * (desktop and CLI), Claude Code, Cline, Zed, Windsurf. Adding a guide for any
 * of those means writing it a capability list of its own - do not reuse this
 * one. Codex desktop is the near miss to re-check: the implementation exists
 * behind an `enable_mcp_apps` flag but ships disabled, so it is "not yet"
 * rather than "no".
 *
 * Check the matrix rather than a blog post before adding a client - the prose
 * list on the MCP Apps overview page omits entries the matrix carries (it is
 * what wrongly excluded Cursor here). The matrix is community-maintained, so
 * treat a host we ship a guide for as needing its own confirmation too.
 */
export const CAPABILITIES: GuideCapability[] = [
  {
    title: "Triage a ranked inbox without leaving the chat",
    body: "The inbox arrives as an interactive dashboard rendered inside the conversation, not as a wall of JSON for the model to summarise. You read, rank and clear threads in place.",
  },
  {
    title: "Draft replies in a real composer",
    body: "Replies open in an editable composer you can correct before anything sends. Nothing leaves your account on the model's say-so alone.",
  },
  {
    title: "Fill and sign PDF attachments",
    body: "Attached forms can be filled in from the thread. Signing is a step you complete yourself - the tool never types a signature on your behalf.",
  },
  {
    title: "Search across threads by meaning, not just keywords",
    body: "Search runs server-side over your mail and returns threads, so the agent works from what is actually in your inbox rather than what it can hold in context.",
  },
];

export const SHARED_FAQ: FaqItem[] = [
  {
    q: "Do I need a DaySurface account?",
    a: "No. There is no account to create and no password to set. You add the server to your client, then sign in to Google when it asks. Authorisation is the whole signup.",
  },
  {
    q: "Is there an API key to paste?",
    a: "No. Auth is OAuth in the browser. If a setup guide anywhere tells you to paste a key, it is not describing this server.",
  },
  {
    q: "What does it get access to?",
    a: "The Google scopes you approve in the consent screen, and nothing else. You can review them at the moment of sign-in and revoke them later from your Google account.",
  },
  {
    q: "Can I run it myself instead?",
    a: "Yes. It is MIT-licensed and self-hostable, and the same tools are exposed over a CLI and a plain HTTP API as well as MCP. Point your client at your own deployment instead of the hosted endpoint.",
  },
];
