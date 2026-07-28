/**
 * Hero section: headline copy, the client-toggle chat mock, and the feature grid.
 */

export interface Feature {
  /** Selects the bespoke diagram in FeatureVisual.astro (by key). */
  visual: string;
  title: string;
  body: string;
}

export const hero: {
  eyebrow: string;
  headline: string;
  subhead: string;
} = {
  // Optional eyebrow pill (launch/funding/release). Set to "" to hide.
  eyebrow: "",
  // Keep the headline short (< ~44 chars) and benefit/identity-driven.
  headline: "Triage, draft, sign - without leaving chat.",
  subhead:
    "A real email composer and a ranked inbox, right inside Claude, ChatGPT, or your favorite agent. Your agent triages and writes; you edit and send.",
};

/**
 * Hero chat mock - a toggle reskins the chat shell to evoke each client while
 * the embedded MCP-app card stays identical (ChatMock.astro). `accent` is a
 * per-client hint applied only to the shell (avatar, top rule); the rendered
 * MCP app stays brand-cyan so it reads as the same app in every client.
 */
export interface ChatClient {
  id: "claude" | "chatgpt" | "goose" | "vscode";
  name: string;
  logo: string;
  accent: string;
}

export const heroChat: { defaultId: ChatClient["id"]; clients: ChatClient[] } = {
  defaultId: "claude",
  clients: [
    { id: "claude", name: "Claude", logo: "/logos/claude.svg", accent: "#d97757" },
    { id: "chatgpt", name: "ChatGPT", logo: "/logos/chatgpt.svg", accent: "#10a37f" },
    { id: "goose", name: "Goose", logo: "/logos/goose.svg", accent: "#e0a458" },
    { id: "vscode", name: "VS Code", logo: "/logos/vscode.svg", accent: "#3794ff" },
  ],
};

export const features: { heading: string; subhead: string; items: Feature[] } = {
  heading: "One codebase, every surface",
  subhead: "Write a tool once. Ship it to agents, scripts, and services without rewrites.",
  items: [
    {
      visual: "transports",
      title: "Three transports, zero duplication",
      body: "Every tool is a pure function in a shared registry, exposed identically over CLI, MCP, and HTTP. Behavior never drifts between interfaces.",
    },
    {
      visual: "interactive",
      title: "Headless or interactive",
      body: "Return data for autonomous agents, or opt into enhanced tools that elicit input, attach media, and render sandboxed UI dashboards.",
    },
  ],
};
