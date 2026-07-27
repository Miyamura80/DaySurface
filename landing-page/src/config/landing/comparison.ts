/**
 * Competitive comparison - powers the on-page comparison section
 * (Comparison.astro), the /compare hub, the per-competitor /vs/<slug> pages,
 * and the machine-readable "How it compares" block in llms-full.txt / agents.md.
 *
 * Editing this one object updates every comparison surface at once.
 *
 * Honesty rules (keep us credible + AEO-citable):
 * - `pickThem` must give a genuine, fair reason to choose the competitor.
 * - Matrix claims should be defensible as of `asOf`; competitor capabilities
 *   shift fast, so date-stamp and keep cells verifiable, not slanted.
 */
export type MatrixState = "yes" | "no" | "partial";

export interface MatrixCell {
  state: MatrixState;
  /** Optional short qualifier shown under the mark. */
  note?: string;
}

export interface MatrixRow {
  /** Capability being compared. */
  capability: string;
  /** One-line explanation of why this capability matters. */
  detail?: string;
  /** Our value. */
  us: MatrixCell;
  /** Per-competitor value, keyed by competitor `id`. */
  cells: Record<string, MatrixCell>;
}

export interface Competitor {
  /** URL slug → /vs/<id>. */
  id: string;
  name: string;
  /** Bucket label, e.g. "Open-source wrapper". */
  category: string;
  /** One line: what they are. */
  blurb: string;
  /** Canonical link to the competitor. */
  url: string;
  /** Headline contrast for the /vs page hero. */
  headline: string;
  /** Paragraph summary of the difference. */
  summary: string;
  /** Honest "when to pick them" - keeps the page credible + citable. */
  pickThem: string;
  /** "when to pick us". */
  pickUs: string;
}

export interface ComparisonPillar {
  title: string;
  body: string;
}

export const comparison: {
  /** Section + page headings. */
  heading: string;
  subhead: string;
  /** Defensibility stamp surfaced on the comparison pages. */
  asOf: string;
  disclaimer: string;
  /** The three headline differentiators. */
  pillars: ComparisonPillar[];
  competitors: Competitor[];
  matrix: MatrixRow[];
} = {
  heading: "How GmailMCP compares",
  subhead:
    "Most Gmail MCPs hand your agent raw API calls and a wall of JSON. GmailMCP is a Gmail product: an interactive inbox you can drive from inside the chat, open source and yours to host.",
  asOf: "June 2026",
  disclaimer:
    "Comparison reflects publicly documented capabilities as of June 2026. The MCP ecosystem moves fast - if something here is out of date, open an issue and we'll fix it.",
  pillars: [
    {
      title: "Interactive UI, not just JSON",
      body: "GmailMCP renders MCP Apps - sandboxed UI that lives inside the chat. Review and edit a draft in a real composer, then triage a ranked inbox in an embedded dashboard, all without leaving your agent. The inbox ranking and triage flow exist because the interactive surface makes them useful; other Gmail MCPs return raw search results and stop there.",
    },
    {
      title: "One codebase, three transports",
      body: "Every tool is a pure function in a shared registry, exposed identically over a CLI, an MCP server, and a plain HTTP API. Build once and call it from your shell, any MCP client, or a script - behavior never drifts between interfaces. Most Gmail MCPs are single-transport: stdio-only, or a hosted endpoint you can't run locally.",
    },
    {
      title: "Open source and self-hostable",
      body: "The whole server is open source and ships with a Dockerfile and deploy config, so you can run it on your own infrastructure with your own OAuth credentials and encrypted token storage. Aggregator gateways route your mail through a proprietary service you don't control.",
    },
  ],
  competitors: [
    {
      id: "gongrzhe-gmail-mcp",
      name: "GongRzhe Gmail-MCP-Server",
      category: "Open-source wrapper",
      blurb:
        "The most-starred open-source Gmail MCP: a local stdio server wrapping the Gmail API.",
      url: "https://github.com/GongRzhe/Gmail-MCP-Server",
      headline: "The open-source Gmail MCP, upgraded.",
      summary:
        "GongRzhe's server is a faithful, well-loved wrapper around the Gmail API - around a dozen tools for send, draft, read, search, labels and attachments, run locally over stdio with a credentials file on disk. You get clean primitives and JSON back. GmailMCP shares the open-source spirit but goes further: it renders an interactive composer and a ranked-inbox dashboard inside the chat, and the same tools are reachable over a CLI and an HTTP API, not just stdio.",
      pickThem:
        "you want a minimal, local, stdio-only Gmail wrapper to embed in a desktop client and you're happy driving everything through JSON tool calls.",
      pickUs:
        "you want an in-chat composer and inbox triage UI, remote zero-install access, and the same tools available over CLI and HTTP as well as MCP.",
    },
    {
      id: "composio-gmail",
      name: "Composio Gmail",
      category: "Aggregator gateway",
      blurb:
        "Gmail as one toolkit inside a managed 500+ app MCP gateway with hosted OAuth.",
      url: "https://composio.dev/toolkits/gmail",
      headline: "A Gmail product, not a Gmail endpoint in a 500-app gateway.",
      summary:
        "Composio's strength is breadth: one managed endpoint and hosted OAuth across hundreds of SaaS apps, with Gmail exposed as a generic search / read / draft / send toolkit. GmailMCP trades breadth for depth on email - an interactive composer and ranked-inbox dashboard rendered in the chat - and it's open source, so you self-host with your own credentials instead of routing mail through a proprietary gateway.",
      pickThem:
        "you need one managed endpoint spanning many SaaS apps and you don't want to run any infrastructure yourself.",
      pickUs:
        "email is the job: you want an interactive in-chat inbox, full control of your own deployment, and the ability to run the same tools over CLI and HTTP.",
    },
    {
      id: "zapier-pipedream-mcp",
      name: "Zapier & Pipedream MCP",
      category: "Workflow automation",
      blurb:
        "Gmail actions inside no-code automation platforms exposed as MCP tools.",
      url: "https://zapier.com/mcp",
      headline: "Built for an agent in the loop, not a no-code workflow.",
      summary:
        "Zapier and Pipedream expose Gmail as actions inside their automation platforms - great for fire-and-forget workflows, with tools often auto-generated from API specs. GmailMCP is purpose-built for a human-in-the-loop agent: an interactive composer where you review and edit before anything sends, a ranked inbox you triage in-chat, and an open-source codebase you host yourself rather than orchestrate through a workflow runner.",
      pickThem:
        "your goal is automated, multi-app workflows triggered by events, and you're already invested in their builder.",
      pickUs:
        "you want an agent that drafts and triages with you interactively, a focused Gmail surface, and a server you own and self-host.",
    },
    {
      id: "google-workspace-mcp",
      name: "Google Workspace MCP",
      category: "Workspace suite",
      blurb:
        "Broad Google Workspace coverage (Gmail, Calendar, Drive, Docs) over MCP.",
      url: "https://github.com/taylorwilsdon/google_workspace_mcp",
      headline: "Gmail done deeply vs. Workspace done broadly.",
      summary:
        "Google Workspace MCP covers a huge surface - Gmail, Calendar, Drive and Docs - as headless tools you run yourself. It's a great fit when you need the whole suite. GmailMCP goes the other way: deep on Gmail with an interactive in-chat composer and ranked-inbox dashboard, a minimal single Gmail scope, and the same tools exposed over CLI and HTTP as well as MCP.",
      pickThem:
        "you need Calendar, Drive and Docs alongside Gmail in one server.",
      pickUs:
        "Gmail is the priority and you want an interactive inbox UI, scope minimalism, and multi-transport access.",
    },
  ],
  matrix: [
    {
      capability: "Interactive in-chat UI (MCP Apps)",
      detail: "Composer + ranked-inbox dashboard rendered inside the chat client.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "JSON only" },
        "composio-gmail": { state: "no", note: "JSON only" },
        "zapier-pipedream-mcp": { state: "no", note: "JSON only" },
        "google-workspace-mcp": { state: "no", note: "JSON only" },
      },
    },
    {
      capability: "Built-in inbox triage & ranking",
      detail: "An importance-ranked inbox out of the box, not just raw search.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "raw search" },
        "composio-gmail": { state: "no", note: "raw search" },
        "zapier-pipedream-mcp": { state: "partial", note: "build a workflow" },
        "google-workspace-mcp": { state: "no", note: "raw search" },
      },
    },
    {
      capability: "One codebase → CLI + MCP + HTTP",
      detail: "Identical tools across every interface, no duplicated logic.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "partial", note: "stdio / HTTP" },
        "composio-gmail": { state: "no", note: "hosted endpoint" },
        "zapier-pipedream-mcp": { state: "no", note: "hosted endpoint" },
        "google-workspace-mcp": { state: "partial", note: "MCP only" },
      },
    },
    {
      capability: "Zero-install (remote streamable HTTP)",
      detail: "Connect by pasting a URL - nothing to install locally.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "local install" },
        "composio-gmail": { state: "yes" },
        "zapier-pipedream-mcp": { state: "yes" },
        "google-workspace-mcp": { state: "no", note: "self-run" },
      },
    },
    {
      capability: "Open source & self-hostable",
      detail: "Run the full server on your own infrastructure.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "yes" },
        "composio-gmail": { state: "no", note: "proprietary" },
        "zapier-pipedream-mcp": { state: "no", note: "proprietary" },
        "google-workspace-mcp": { state: "yes" },
      },
    },
    {
      capability: "Minimal scope + encrypted tokens",
      detail: "A single Gmail scope with tokens encrypted at rest.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "partial", note: "local token file" },
        "composio-gmail": { state: "yes", note: "managed" },
        "zapier-pipedream-mcp": { state: "yes", note: "managed" },
        "google-workspace-mcp": { state: "partial", note: "broad scopes" },
      },
    },
  ],
};
