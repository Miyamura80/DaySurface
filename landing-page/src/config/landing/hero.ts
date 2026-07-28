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
  heading: "Every action lands somewhere you can edit.",
  subhead:
    "Replies open in a real composer, triage banks what your agent already read, and PDFs get filled for you to sign. Nothing leaves your account until you send it.",
  items: [
    {
      visual: "composer",
      title: "Drafts you edit, not approve",
      body: "Replies land in a real Gmail draft with a composer rendered inside the chat - recipients, subject, body and attachments, all editable in place. Your agent writes the first version; nothing sends until you press send.",
    },
    {
      visual: "ledger",
      title: "Triage that remembers",
      body: "Your agent reads a thread once and banks its verdict. Each judgment is stamped against the thread's Gmail history, so the next \"what needs me?\" only re-reads what actually changed - instead of re-running a search and starting from zero.",
    },
    {
      visual: "signing",
      title: "Fill and sign, in the thread",
      body: "Your agent opens a PDF attachment, fills its form fields, and hands it back for signature. The bytes stay server-side and never enter the conversation - and signing is a step only you can complete, by typing your own name.",
    },
  ],
};
