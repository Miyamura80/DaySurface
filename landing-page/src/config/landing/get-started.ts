/**
 * "Get started" - the consolidated transports + onboarding section, plus the
 * compatibility strip and the client install picker.
 */
import { site } from "./site";

/**
 * One service registry, three ways to call it. The visitor picks a transport
 * (CLI / MCP / HTTP API) and that one choice drives BOTH steps:
 *   step 1 - how you connect / install for that transport
 *            (MCP expands into a client sub-picker - see ConnectWidget)
 *   step 2 - calling the *same* tool (gmail_curate_inbox) over that transport,
 *            so the "identical behavior, three transports" payoff is visible
 *            the moment you toggle.
 * Keep the step-2 example identical across transports - that parallel IS the pitch.
 */
export interface TransportOption {
  id: "cli" | "mcp" | "api";
  label: string;
  /** Icon in public/logos/, rendered monochrome next to the label. */
  icon: string;
  // Step 1 - connect / install
  setupTitle: string;
  setupBody: string;
  /** "connect" swaps the code panel for the interactive client picker. */
  setupKind: "code" | "connect";
  setupCode?: string;
  setupLang?: string;
  // Step 2 - call a tool
  callTitle: string;
  callBody: string;
  callCode: string;
  callLang: string;
  /**
   * Optional sub-toggle inside step 02 (e.g. MCP headless vs. interactive).
   * When present, the toggle renders below callBody and each variant supplies
   * its own code panel; callCode/callLang act as the fallback for no-JS.
   */
  callVariants?: CallVariant[];
}

/** A toggle option within a transport's "call a tool" step. */
export interface CallVariant {
  id: string;
  label: string;
  /** One-line description shown above the variant's output. */
  body: string;
  /**
   * "code" renders a syntax-highlighted block (headless → JSON output).
   * "app" renders the live MCP-app card (interactive → the same UI as the hero).
   */
  kind: "code" | "app";
  /** Required when kind === "code". */
  code?: string;
  lang?: string;
}

export const getStarted: {
  heading: string;
  subhead: string;
  defaultId: TransportOption["id"];
  transports: TransportOption[];
} = {
  heading: "One tool. Three transports.",
  subhead:
    "Write a service once and call it identically from the CLI, any MCP client, or plain HTTP. Same inputs, same outputs, zero duplicated logic. Pick yours to get set up.",
  defaultId: "mcp",
  transports: [
    {
      id: "cli",
      label: "CLI",
      icon: "/logos/cli.svg",
      setupTitle: "Install the CLI",
      setupBody:
        "Clone the template and sync dependencies with uv. The daysurface command is ready to run.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `git clone https://github.com/Miyamura80/DaySurface
cd DaySurface && make all`,
      callTitle: "Call a tool",
      callBody: "Invoke any service straight from your shell: typed inputs, structured output.",
      callLang: "bash",
      callCode: `$ daysurface gmail-curate-inbox --limit 3

0.86  Re: Q3 contract redlines        legal@acme.com    ✎ draft
0.61  Design review for v2 dashboard  sarah@team.io
0.42  Your invoice is ready           billing@stripe.com`,
    },
    {
      id: "mcp",
      label: "MCP",
      icon: "/logos/mcp.svg",
      setupTitle: "Add it to your client",
      setupBody:
        "The server runs over streamable HTTP, so onboarding is just its URL. Pick your client: one click where deep links are supported, copy-and-paste everywhere else.",
      setupKind: "connect",
      callTitle: "Call a tool",
      callBody:
        "Your agent discovers the tools automatically and calls them with typed inputs. The same service answers two ways:",
      callLang: "jsonc",
      callCode: `// client → server  ·  JSON-RPC over streamable HTTP
{
  "method": "tools/call",
  "params": {
    "name": "gmail_curate_inbox",
    "arguments": { "limit": 3 }
  }
}`,
      callVariants: [
        {
          id: "headless",
          label: "Headless",
          kind: "code",
          body:
            "The default: a pure service returns its typed output model. FastMCP derives the outputSchema, so the CLI, API, and MCP all behave identically.",
          lang: "jsonc",
          code: `// client → server  ·  JSON-RPC over streamable HTTP
{
  "method": "tools/call",
  "params": {
    "name": "gmail_curate_inbox",
    "arguments": { "limit": 3 }
  }
}

// server → client  ·  structured output
{
  "content": [{ "type": "text", "text": "3 threads ranked. Top: Q3 contract redlines" }],
  "structuredContent": {
    "threads": [
      { "subject": "Re: Q3 contract redlines", "importance_score": 0.86, "has_draft": true },
      { "subject": "Design review for v2 dashboard", "importance_score": 0.61, "has_draft": false }
    ]
  }
}`,
        },
        {
          id: "interactive",
          label: "Interactive",
          kind: "app",
          body:
            "Opt the same tool into an @enhance handler and it can elicit input, attach media, or render an MCP App: a sandboxed iframe dashboard your client embeds inline. MCP-only; the CLI and API stay untouched.",
        },
      ],
    },
    {
      id: "api",
      label: "HTTP API",
      icon: "/logos/api.svg",
      setupTitle: "Point at the endpoint",
      setupBody:
        "No install required: the HTTP API is live at its own host. Authenticate with a bearer token and call it from anything.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `export DAYSURFACE_API_URL=${site.apiUrl}
export TOKEN=sk-...   # OAuth 2.1 bearer`,
      callTitle: "Call a tool",
      callBody:
        "Hit the same service over plain HTTP: identical inputs and outputs as the CLI and MCP.",
      callLang: "bash",
      callCode: `$ curl -s $DAYSURFACE_API_URL/api/v1/services/gmail_curate_inbox \\
    -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{ "limit": 3 }'

{ "threads": [
    { "subject": "Re: Q3 contract redlines", "importance_score": 0.86, "has_draft": true },
    { "subject": "Design review for v2 dashboard", "importance_score": 0.61, "has_draft": false }
] }`,
    },
  ],
};

/**
 * Compatibility / trust strip. Doubles as a capability signal for MCP.
 * Logos live in `public/logos/` and are rendered flattened to a single brand
 * color via CSS mask (see TrustStrip.astro) so full-color marks don't clash
 * with the monochrome Hackbox aesthetic. `logo: null` renders a text monogram
 * fallback - drop an SVG in `public/logos/` and point `logo` at it to upgrade.
 */
export interface Client {
  name: string;
  logo: string | null;
}

export const compatibility: { heading: string; clients: Client[] } = {
  heading: "Works with every MCP client",
  clients: [
    { name: "Claude", logo: "/logos/claude.svg" },
    { name: "Codex", logo: "/logos/codex.svg" },
    { name: "Cursor", logo: "/logos/cursor.svg" },
    { name: "ChatGPT", logo: "/logos/chatgpt.svg" },
    { name: "VS Code", logo: "/logos/vscode.svg" },
    { name: "OpenClaw", logo: "/logos/openclaw.svg" },
    { name: "Goose", logo: "/logos/goose.svg" },
  ],
};

/**
 * Client picker for the "Add it to your client" step (see ConnectWidget.astro).
 *
 * method "deeplink" → a real one-click install URL is built at build time from
 *   site.mcpUrl + site.serverName (Cursor/VS Code/Goose support this).
 * method "manual" → no deep link exists (Claude, ChatGPT), so we show the
 *   server URL to copy plus the click-path to paste it. `steps` are those.
 *
 * Deep-link formats verified against official docs (cursor.com, code.visualstudio.com,
 * goose docs). Claude/ChatGPT have no install URL scheme - paste-the-URL is the
 * only supported flow.
 */
export interface InstallTarget {
  id: string;
  name: string;
  logo: string;
  /**
   * How this client gets connected:
   * - "deeplink" - a one-click install URL scheme (Cursor / VS Code / Goose).
   * - "manual"   - no URL scheme; the user pastes into a settings screen, so we
   *                spell out the click-path (Claude / ChatGPT web).
   * - "prompt"   - agentic clients that can configure themselves; we hand the
   *                user a prompt to paste into the agent instead of a click-path.
   */
  method: "deeplink" | "manual" | "prompt";
  /** For manual targets: the click-path to paste the URL. */
  steps?: string[];
  /** For prompt targets: the text the user pastes into their agent. */
  setupPrompt?: string;
  /** Optional note rendered under a deep-link button or setup prompt. */
  note?: string;
}

/**
 * Paste-into-your-agent setup prompt for clients with no install URL scheme.
 *
 * Deliberately tells the agent to look up its OWN config shape rather than
 * hardcoding one: every client spells this differently - VS Code nests under
 * `servers`, Cursor and Cline under `mcpServers`, Zed under `context_servers`,
 * and Windsurf wants `serverUrl` where everyone else wants `url`. A snippet
 * copied between clients fails silently, so the prompt names the trap.
 */
const AGENT_SETUP_PROMPT = `Add a remote MCP server, then verify it connects.

  Name:      ${site.serverName}
  Endpoint:  ${site.mcpUrl}
  Transport: streamable HTTP (remote, not stdio)
  Auth:      OAuth in the browser, no key to paste

Use THIS client's own config keys - they differ:
VS Code "servers", Cursor/Cline "mcpServers",
Zed "context_servers", Windsurf "serverUrl" not "url".
Keep existing servers. Show me the diff, then say
what to click to finish sign-in.`;

export const connect: {
  mcpUrl: string;
  serverName: string;
  /** id of the target selected by default in the dropdown. */
  defaultId: InstallTarget["id"];
  targets: InstallTarget[];
} = {
  mcpUrl: site.mcpUrl,
  serverName: site.serverName,
  // Chat clients first - they are who the hero is written for. Editors and
  // coding agents follow.
  defaultId: "claude",
  targets: [
    {
      id: "claude",
      name: "Claude",
      logo: "/logos/claude.svg",
      method: "deeplink",
      note: "Opens Claude with the name and URL already filled in - just click Add. Claude flags it as suggested by an external link; that's expected. On Team and Enterprise plans an admin adds it.",
      // Fallback for anyone the deep link drops on the wrong screen (e.g.
      // signed out, or a client that strips the hash fragment).
      steps: [
        "Open Claude → Settings → Connectors",
        "Click “Add custom connector”",
        "Paste the URL above, then click Add and complete sign-in",
      ],
    },
    {
      id: "chatgpt",
      name: "ChatGPT",
      logo: "/logos/chatgpt.svg",
      // No install URL scheme exists for ChatGPT - manual only. Re-checked
      // July 2026: no deep link is documented by OpenAI, none is in community
      // use, and no "Add to ChatGPT" badge exists in an ecosystem where the
      // equivalents for VS Code, Cursor and LM Studio are commonplace.
      // Developer mode is a prerequisite, so a deep link would land most users
      // on a screen with no Create button anyway.
      method: "manual",
      steps: [
        "Settings → Connectors (newer builds: Apps & Connectors)",
        "Advanced settings → turn on Developer mode",
        "Back on Connectors, click Create",
        "Paste the URL above, name it, then click Create",
        "Start a new chat so the tools menu refreshes",
      ],
    },
    {
      id: "claude-code",
      name: "Claude Code",
      logo: "/logos/cli.svg",
      method: "prompt",
      setupPrompt: `claude mcp add --transport http --scope user ${site.serverName} ${site.mcpUrl}`,
      note: "Run it in your terminal, then /mcp in a session to sign in.",
    },
    {
      id: "cursor",
      name: "Cursor",
      logo: "/logos/cursor.svg",
      method: "deeplink",
      note: "Opens Cursor and adds the server. Not working? Copy the URL above and add it under Settings → MCP.",
    },
    {
      id: "vscode",
      name: "VS Code",
      logo: "/logos/vscode.svg",
      method: "deeplink",
      note: "Opens VS Code and adds the server. Requires Copilot agent mode.",
    },
    {
      id: "goose",
      name: "Goose",
      logo: "/logos/goose.svg",
      method: "deeplink",
      note: "Opens Goose and adds the extension over streamable HTTP.",
    },
    {
      id: "cline",
      name: "Cline",
      logo: "/logos/mcp.svg",
      method: "prompt",
      setupPrompt: AGENT_SETUP_PROMPT,
      note: "Or add it by hand: MCP Servers → Remote Servers → Streamable HTTP.",
    },
    {
      id: "zed",
      name: "Zed",
      logo: "/logos/mcp.svg",
      method: "prompt",
      setupPrompt: AGENT_SETUP_PROMPT,
      note: "Or add it by hand: Settings → AI → MCP Servers → Add Remote Server.",
    },
    {
      id: "windsurf",
      name: "Windsurf",
      logo: "/logos/mcp.svg",
      method: "prompt",
      setupPrompt: AGENT_SETUP_PROMPT,
      note: "Windsurf uses “serverUrl”, not “url” - a snippet copied from another client will fail silently.",
    },
    {
      id: "other",
      name: "Any MCP client",
      logo: "/logos/mcp.svg",
      method: "prompt",
      setupPrompt: AGENT_SETUP_PROMPT,
      note: "Anything that speaks MCP over streamable HTTP works - there is nothing to install.",
    },
  ],
};
