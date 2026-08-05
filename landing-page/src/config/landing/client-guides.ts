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

/**
 * Shared across every guide, and ONLY valid for hosts that render MCP Apps.
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
  {
    slug: "vscode",
    targetId: "vscode",
    clientName: "VS Code",
    title: "Connect Gmail to VS Code Copilot - DaySurface",
    description:
      "Give GitHub Copilot agent mode access to your Gmail over MCP: add one remote server, sign in with Google, and triage your inbox without leaving the editor.",
    heading: "Connect Gmail to VS Code",
    subhead: "One MCP server in agent mode, and Copilot can read and answer your mail.",
    lede: "VS Code reaches outside tools through MCP servers in Copilot agent mode, so adding DaySurface gives Copilot your inbox alongside your code. VS Code also renders MCP Apps, which means the inbox arrives as an actual dashboard in the chat panel rather than as JSON for the model to paraphrase. There is nothing to install beyond the server entry itself.",
    prerequisites: [
      "A Google account with Gmail.",
      "GitHub Copilot in VS Code, with agent mode available. MCP servers are an agent-mode feature - they do not appear in ask or edit mode.",
    ],
    steps: [
      {
        title: "Switch Copilot Chat to agent mode",
        body: "Open the Copilot Chat panel and pick Agent from the mode selector. MCP tools are only offered in agent mode, so a server added while you are in ask mode will look like it did not work.",
      },
      {
        title: "Add the server",
        body: "Open the Command Palette and run MCP: Add Server, choose the HTTP option, and paste the DaySurface endpoint. This writes an entry to your MCP configuration - pick the user-level scope if you want it available across every workspace rather than one project.",
      },
      {
        title: "Sign in with Google",
        body: "Start the server and complete the Google sign-in it opens in your browser. Review the scopes on the consent screen before approving. There is no key or token to paste back into VS Code.",
      },
      {
        title: "Check the tools are there",
        body: "Open the tools picker in the Copilot Chat panel and confirm the DaySurface tools are listed and enabled. If the list looks stale, reload the window.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Why do I have to be in agent mode?",
        a: "MCP tools are wired into agent mode specifically. Ask and edit mode do not call external tools, so the server will connect fine and still appear to do nothing until you switch.",
      },
      {
        q: "Can I share the server with my team through the repo?",
        a: "Yes. A workspace-scoped MCP entry can be committed so everyone who opens the project gets the server. Each person still signs in to their own Google account - nothing about the shared config shares mail access.",
      },
      ...SHARED_FAQ,
    ],
  },
  {
    slug: "goose",
    targetId: "goose",
    clientName: "Goose",
    title: "Connect Gmail to Goose - DaySurface",
    description:
      "Add DaySurface to Goose as a streamable-HTTP extension: sign in with Google, then triage your inbox and draft replies in an interactive dashboard inside Goose.",
    heading: "Connect Gmail to Goose",
    subhead: "One streamable-HTTP extension, and Goose can work your inbox.",
    lede: "Goose calls MCP servers extensions, and it renders MCP Apps natively - it fetches a tool's ui:// resource and mounts it in a sandboxed iframe. That makes it one of the hosts where the ranked inbox and the composer show up as real interfaces rather than as text. Adding DaySurface is a single remote extension over streamable HTTP.",
    prerequisites: [
      "A Google account with Gmail.",
      "Goose, desktop or CLI. The desktop app is the one that renders the dashboards.",
    ],
    steps: [
      {
        title: "Open the extensions settings",
        body: "In Goose, go to Settings and then Extensions. This is where both bundled and remote extensions are managed.",
      },
      {
        title: "Add a remote extension",
        body: "Choose to add a custom extension and set its type to streamable HTTP. Goose also supports stdio extensions that run a local process - that is the wrong type here, since DaySurface is a remote server.",
      },
      {
        title: "Paste the endpoint and name it",
        body: "Give the extension a name and paste the DaySurface endpoint as its URL. Leave it enabled when you save.",
      },
      {
        title: "Sign in with Google",
        body: "Goose will take you through the OAuth flow in your browser on first use. Review the scopes before approving them.",
      },
      {
        title: "Ask it about your inbox",
        body: "Start a session and ask what needs you today. In the desktop app the inbox comes back as a dashboard you can act in, not a list you have to read.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Does the inbox dashboard work in the Goose CLI?",
        a: "No. The interactive surfaces need a host that can mount an iframe, which means the desktop app. The CLI still gets every tool, it just gets them as text and structured results.",
      },
      {
        q: "Is this a stdio extension or a remote one?",
        a: "Remote, over streamable HTTP. There is no local process to run and nothing to install, so picking the stdio type in the extension dialog will not work.",
      },
      ...SHARED_FAQ,
    ],
  },
  {
    slug: "cursor",
    targetId: "cursor",
    clientName: "Cursor",
    title: "Connect Gmail to Cursor - DaySurface",
    description:
      "Add DaySurface to Cursor as a remote MCP server: sign in with Google, then read threads, draft replies and clear your inbox without leaving the editor.",
    heading: "Connect Gmail to Cursor",
    subhead: "One remote MCP server, and Cursor's agent can work your inbox.",
    lede: "Cursor talks to outside tools over MCP, and it renders MCP Apps, so the inbox comes back as something you can act in rather than a blob of JSON in the agent transcript. Adding DaySurface is one remote server entry - there is no local process to run and no key to paste.",
    prerequisites: [
      "A Google account with Gmail.",
      "Cursor, with the agent available. MCP tools are called by the agent, not by inline completions.",
    ],
    steps: [
      {
        title: "Open the MCP settings",
        body: "In Cursor, go to Settings and then MCP. This lists the servers the agent can reach and is where a new one is added.",
      },
      {
        title: "Add the server",
        body: "Add a new MCP server, choose the remote or HTTP option, and paste the DaySurface endpoint. Cursor writes this to an mcp.json - user-level makes it available everywhere, project-level scopes it to one repo.",
      },
      {
        title: "Sign in with Google",
        body: "Cursor opens the OAuth flow in your browser the first time the server is used. Check the scopes on the consent screen before approving them.",
      },
      {
        title: "Check the tools are listed",
        body: "Back in MCP settings, the server should show as connected with its tools enumerated. If it looks stuck, toggling the server off and on again re-runs the handshake.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Cursor uses \"mcpServers\" - can I copy a config from another editor?",
        a: "Not safely. The key names differ between clients: Cursor and Cline use mcpServers, VS Code uses servers, Zed uses context_servers, and Windsurf wants serverUrl where the others want url. A snippet pasted across clients tends to fail silently rather than error.",
      },
      {
        q: "Should I commit the server to my repo?",
        a: "You can, via a project-level mcp.json, and everyone who opens the project gets the server. Each person still signs in to their own Google account - sharing the config never shares mail access.",
      },
      ...SHARED_FAQ,
    ],
  },
];

/** Lookup used by `getStaticPaths` and the markdown twin. */
export function clientGuideBySlug(slug: string): ClientGuide | undefined {
  return clientGuides.find((g) => g.slug === slug);
}
