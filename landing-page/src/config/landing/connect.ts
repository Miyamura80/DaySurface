/**
 * The connect-your-client picker (ConnectWidget.astro), rendered as the primary
 * CTA in the hero and repeated in the final CTA.
 *
 * This file previously also held the "One tool. Three transports." onboarding
 * section, which was removed: its MCP step duplicated the hero's connect widget,
 * and its CLI/HTTP examples pitched the template's architecture to an audience
 * that came here for their inbox. That story now lives where it earns its place
 * - the comparison pillars and the /api page.
 */
import { site } from "./site";

/**
 * Compatibility / trust strip. Doubles as a capability signal for MCP.
 * Logos live in `public/logos/` and are rendered flattened to a single brand
 * color via CSS mask (see TrustStrip.astro) so full-color marks don't clash
 * with the monochrome Hackbox aesthetic. `logo: null` renders a text monogram
 * fallback - drop an SVG in `public/logos/` and point `logo` at it to upgrade.
 */
export interface Host {
  name: string;
  logo: string | null;
}

export const compatibility: { heading: string; hosts: Host[] } = {
  heading: "Works with every MCP client",
  hosts: [
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
 * method "deeplink" → a real install URL is built at build time from
 *   site.mcpUrl + site.serverName (Cursor/VS Code/Goose support this).
 * method "manual" → no deep link exists, so we show the server URL to copy plus
 *   the click-path to paste it. `steps` are those.
 *
 * A deep link does not always mean one click: ChatGPT's opens the create-
 * connector dialog but prefills nothing, so it carries `prefills: false` and
 * keeps its full click-path in `steps`. See `prefills` below.
 *
 * Deep-link formats verified against official docs (cursor.com, code.visualstudio.com,
 * goose docs) and by clicking them.
 */

export interface InstallTarget {
  id: string;
  name: string;
  logo: string;
  /**
   * How this host gets connected:
   * - "deeplink" - an install URL that opens the host on the right screen
   *                (Claude / ChatGPT / Cursor / VS Code / Goose). Whether it
   *                also fills the form in is `prefills`.
   * - "manual"   - no install URL; the user pastes into a settings screen, so we
   *                spell out the click-path. No target uses this today (ChatGPT
   *                was the last, and now has a navigating deep link) - it stays
   *                for the next host that ships no URL scheme at all.
   * - "prompt"   - agentic hosts that can configure themselves; we hand the user
   *                something to paste instead of a click-path. See `setupKind`,
   *                because "paste this" means different things per host.
   */
  method: "deeplink" | "manual" | "prompt";
  /**
   * Deep-link targets only; defaults to true. `false` means the link navigates
   * to the right dialog but leaves every field blank (ChatGPT), so the visitor
   * still copies the server URL. The UI drops its "1-click" promise and the
   * agent-facing copy says the URL has to be pasted. Getting this wrong is
   * worse than having no deep link: it tells someone the job is done when the
   * dialog in front of them is empty.
   */
  prefills?: boolean;
  /**
   * For prompt targets, what `setupPrompt` actually is:
   * - "prompt"  (default) - natural language to paste into the agent's chat.
   * - "command" - a shell command to run in a terminal. Claude Code takes one
   *               line rather than a paragraph, and telling someone to "paste
   *               this into Claude Code" would send them to the wrong place.
   */
  setupKind?: "prompt" | "command";
  /**
   * The click-path to paste the URL: the whole flow for manual targets, and the
   * fallback (or, when `prefills` is false, the rest of the job) for deep links.
   */
  steps?: string[];
  /** For prompt targets: the text the user pastes (see `setupKind`). */
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
      // OpenAI publishes no install URL *scheme* - there is no name/url pair to
      // hand it - but the Connectors settings route does take a
      // `create-connector` flag, which is enough to open the New Plugin dialog
      // directly. That is a navigation shortcut, not an install: the dialog
      // opens empty, so `prefills` is false and every field below still has to
      // be filled in.
      method: "deeplink",
      prefills: false,
      note: "Opens ChatGPT's New Plugin dialog directly - the fields come up empty, so paste the URL above, set Authentication to OAuth, tick the risk box, then Create. Developer mode has to be on first (Settings → Connectors → Advanced settings), and custom connectors need a paid plan.",
      steps: [
        "Settings → Connectors (newer builds: Apps & Connectors)",
        "Advanced settings → turn on Developer mode",
        "Back on Connectors, click Create",
        "Paste the URL above, name it, set Authentication to OAuth",
        "Tick “I understand and want to continue”, then click Create",
        "Start a new chat so the tools menu refreshes",
      ],
    },
    {
      id: "claude-code",
      name: "Claude Code",
      logo: "/logos/cli.svg",
      method: "prompt",
      setupKind: "command",
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
