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
