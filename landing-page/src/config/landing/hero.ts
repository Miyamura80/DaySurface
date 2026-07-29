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
  // Pays off the hero headline verb by verb: draft, triage, sign. Keep it to
  // end-user outcomes - the transports story lives in the comparison pillars
  // and on /api, and the visuals here must not restage the hero chat mock
  // (which already renders the composer and the ranked inbox).
  //
  // The diagram carries each claim (see components/diagrams/), so the body is
  // its caption, not its transcript: one sentence, ~15 words, hard ceiling.
  // If a sentence explains the picture, cut it - the picture failed or the
  // sentence is redundant.
  heading: "Every action lands somewhere you can edit.",
  subhead: "Your agent drafts, triages and fills. You edit and send.",
  items: [
    {
      visual: "composer",
      title: "Drafts you edit, not approve",
      body: "Replies open in a real Gmail draft inside the chat. Nothing sends until you do.",
    },
    {
      visual: "ledger",
      title: "Triage that remembers",
      body: "Verdicts are banked per thread, so the next pass re-reads only what changed.",
    },
    {
      visual: "signing",
      title: "Fill and sign, in the thread",
      body: "Your agent fills the PDF where it lives. The file never enters the chat, and only you can sign it.",
    },
  ],
};
