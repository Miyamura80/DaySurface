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
import { type ClientGuide, CAPABILITIES, SHARED_FAQ } from "./client-guides-shared";

export type { ClientGuide, GuideStep, GuideCapability } from "./client-guides-shared";

/**
 * Every guide, ordered by who the reader is most likely to be. That order is
 * also the sitemap order and the order of the "keep reading" links at the foot
 * of each page, so the mainstream assistants stay the obvious next click.
 *
 * Postman and MCPJam had guides here and were removed deliberately. Both render
 * MCP Apps, so they qualified on capability, but neither is a place anyone reads
 * their mail - they are for inspecting a server - and "connect gmail to postman"
 * has no search behind it. Capability alone is not a reason to ship a page.
 */
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
  {
    slug: "microsoft-365-copilot",
    // Null on purpose - see the field's doc comment. The install picker cannot
    // offer a tenant-admin provisioning flow, so this guide is the only place
    // M365 Copilot is documented.
    targetId: null,
    clientName: "Microsoft 365 Copilot",
    title: "Connect Gmail to Microsoft 365 Copilot - DaySurface",
    description:
      "Add the DaySurface Gmail MCP server to Microsoft 365 Copilot as a custom connector, or as an MCP tool on a Copilot Studio agent. Requires a tenant administrator.",
    heading: "Connect Gmail to Microsoft 365 Copilot",
    subhead: "An admin adds this one - there is no per-user settings screen.",
    lede: "Microsoft 365 Copilot reaches third-party MCP servers through connectors managed at the tenant level, so this is not something an individual turns on for themselves. There are two documented routes: a custom connector in the Microsoft 365 admin center, which makes the server available across the tenant, or an MCP tool on a specific Copilot Studio agent. Both need elevated rights.",
    prerequisites: [
      "A Google account with Gmail.",
      "Global Administrator or AI Administrator in the Microsoft 365 admin center for the connector route.",
      "For the admin-center route, authentication configured in advance - the connector form asks for a registration ID, not a URL and a password.",
    ],
    steps: [
      {
        title: "Open the Copilot connectors gallery",
        body: "Sign in to the Microsoft 365 admin center and go to Copilot, then Connectors. Open the Gallery tab.",
      },
      {
        title: "Start a custom connector",
        body: "Under Created by your org, find the Create a new connector tile and select Add.",
      },
      {
        title: "Point it at the MCP server",
        body: "On the custom connector page, under Connect to MCP server, select Add and supply the DaySurface endpoint along with the registration ID matching your authentication method - the SSO or OAuth registration ID from the Teams Developer Portal.",
      },
      {
        title: "Save and roll out",
        body: "Save to create the connector. It then follows your tenant's normal rollout and consent path rather than appearing instantly for everyone.",
      },
      {
        title: "Or add it to one Copilot Studio agent instead",
        body: "If you want it scoped to a single agent rather than the tenant, open that agent's Tools page in Copilot Studio, choose Add a tool, then New tool, then MCP, and run the onboarding wizard with the endpoint and OAuth 2.0.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Can I add this myself without an admin?",
        a: "No. Both documented routes need elevated rights - Global or AI Administrator for a tenant connector, and maker access to the agent for the Copilot Studio route. If you want DaySurface on your own account today, use a client you control, such as Claude or ChatGPT.",
      },
      {
        q: "Microsoft's guidance mentions read-only tools. Does that rule this out?",
        a: "That guidance is about federated connectors used for grounding, where the tools are search and fetch. DaySurface exposes tools that change mailbox state as well, so check the route you pick against your own tenant policy before rolling it out - and note that sending is gated behind a draft you approve rather than happening on the model's judgement.",
      },
      ...SHARED_FAQ,
    ],
  },
];

/** Lookup used by `getStaticPaths` and the markdown twin. */
export function clientGuideBySlug(slug: string): ClientGuide | undefined {
  return clientGuides.find((g) => g.slug === slug);
}
