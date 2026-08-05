/**
 * Content for `/connect-gmail-to-<client>`, one standalone guide per chat client.
 *
 * These exist for the same reason `/gmail-webhooks` does. "Connect Gmail to
 * Claude" and "connect Gmail to ChatGPT" are the highest-intent queries this
 * product can answer - someone typing one is trying to do the exact thing the
 * server does - and today they land on either the homepage, which pitches before
 * it answers, or `/docs/mcp/setup`, which is written for a reader who has already
 * chosen a server. Neither answers the question in its first screen.
 *
 * Keep these distinct from `/connect`. That page answers "is there an account to
 * create?" for every client at once and canonicalises a dozen aliases onto
 * itself; these answer one client's setup question in depth and carry that
 * client's name. Two same-origin URLs with the same framing would compete for
 * one query, which is the trap `webhooks.ts` documents.
 *
 * The step text is deliberately derived from the same facts as `connect.ts`
 * rather than restated freehand: if the ChatGPT dialog stops opening empty, the
 * install widget and this guide must not disagree about it.
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
  /** Matching `connect.ts` target id, so the two can be cross-checked. */
  targetId: string;
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

const CAPABILITIES: GuideCapability[] = [
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

const SHARED_FAQ: FaqItem[] = [
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

export const clientGuides: ClientGuide[] = [
  {
    slug: "claude",
    targetId: "claude",
    clientName: "Claude",
    title: "Connect Gmail to Claude - DaySurface",
    description:
      "Give Claude access to your Gmail over MCP: add one remote server, sign in with Google, then triage your inbox and draft replies inside the chat. No API key, no account to create.",
    heading: "Connect Gmail to Claude",
    subhead: "One remote MCP server, a Google sign-in, and Claude can work your inbox.",
    lede: "Claude has no built-in Gmail integration. What it does have is MCP, a protocol for pointing it at an outside tool, and DaySurface is an MCP server for Gmail. You add the server once, approve the Google scopes, and Claude gains a set of Gmail tools it can use in any conversation. There is nothing to install locally and no key to paste.",
    prerequisites: [
      "A Google account with Gmail.",
      "Claude on a plan that allows custom connectors. On Team and Enterprise, an admin adds connectors on the workspace's behalf.",
    ],
    steps: [
      {
        title: "Open the connector settings",
        body: "In Claude, go to Settings, then Connectors. This is where remote MCP servers are added. If you do not see Connectors, your plan or your workspace admin controls them.",
      },
      {
        title: "Add the server",
        body: "Click Add custom connector and paste the DaySurface endpoint. Claude will flag it as suggested by an external link, which is expected for any connector added from outside the client.",
      },
      {
        title: "Sign in with Google",
        body: "Click Add, then complete the Google sign-in Claude opens. Review the scopes on the consent screen before you approve them. This is the only credential step, and it replaces having an account.",
      },
      {
        title: "Start a new conversation",
        body: "Open a fresh chat so the tool list refreshes, then ask Claude about your inbox. If the tools do not appear, a new conversation is almost always the fix.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Does this work in the Claude desktop app and on the web?",
        a: "Yes. The connector is attached to your Claude account rather than to one device, so it follows you across the web app and the desktop app once it is added.",
      },
      {
        q: "Does it work in Claude Code?",
        a: "Yes, but it is added differently. Claude Code takes a single terminal command rather than a settings screen, then /mcp inside a session to sign in.",
      },
      ...SHARED_FAQ,
    ],
  },
  {
    slug: "chatgpt",
    targetId: "chatgpt",
    clientName: "ChatGPT",
    title: "Connect Gmail to ChatGPT - DaySurface",
    description:
      "Give ChatGPT access to your Gmail over MCP: turn on Developer mode, create a connector pointing at the DaySurface endpoint, sign in with Google, and work your inbox from the chat.",
    heading: "Connect Gmail to ChatGPT",
    subhead: "Developer mode, one connector, a Google sign-in. No key to paste.",
    lede: "ChatGPT reaches outside tools through connectors, which speak MCP. DaySurface is an MCP server for Gmail, so adding it as a connector is what gives ChatGPT your inbox. The one wrinkle is that connector creation lives behind Developer mode, and the dialog opens empty - so unlike some clients, you will be pasting the endpoint in by hand.",
    prerequisites: [
      "A Google account with Gmail.",
      "A ChatGPT plan that exposes Connectors and Developer mode.",
    ],
    steps: [
      {
        title: "Turn on Developer mode",
        body: "Go to Settings, then Connectors, then Advanced settings, and switch Developer mode on. Connector creation does not appear until you do - this is the step people miss.",
      },
      {
        title: "Create the connector",
        body: "Back on the Connectors screen, click Create. The dialog opens with every field blank, so have the DaySurface endpoint ready to paste rather than expecting it to be filled in for you.",
      },
      {
        title: "Fill in the details",
        body: "Paste the endpoint, give the connector a name, and set Authentication to OAuth. Do not look for an API key field - there is no key, and picking the wrong auth mode here is the most common reason setup fails.",
      },
      {
        title: "Accept and create",
        body: "Tick the risk acknowledgement, then click Create. ChatGPT will take you through the Google sign-in, where you can review the scopes before approving.",
      },
      {
        title: "Start a new chat",
        body: "Open a new conversation so the tools menu refreshes. The connector will not appear in a chat that was already open when you created it.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Why do I need Developer mode?",
        a: "OpenAI keeps custom connector creation behind it. It is a setting on your own account rather than anything specific to this server, and you only turn it on once.",
      },
      {
        q: "The dialog opened empty - did the link fail?",
        a: "No. OpenAI publishes no install URL scheme that carries a name and endpoint, so the shortcut can open the right dialog but cannot prefill it. Pasting the endpoint yourself is the expected flow.",
      },
      ...SHARED_FAQ,
    ],
  },
];

/** Lookup used by `getStaticPaths` and the markdown twin. */
export function clientGuideBySlug(slug: string): ClientGuide | undefined {
  return clientGuides.find((g) => g.slug === slug);
}
