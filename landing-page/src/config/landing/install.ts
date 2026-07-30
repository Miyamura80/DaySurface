/**
 * The client install matrix - one flat, serializable record per supported MCP
 * client, built at build time from `connect.ts` + `utils/deeplink.ts`.
 *
 * This exists because the same matrix has to reach four audiences and every
 * hand-rolled copy drifted:
 *
 *   connect.ts + deeplink.ts
 *            │
 *            ▼
 *   buildInstallMatrix()
 *            │
 *   ┌────────┼──────────────┬────────────────────┐
 *   ▼        ▼              ▼                    ▼
 * WebMcp   connect.astro  connect.md      install.json
 * (browser (humans)      (LLM agents)    (programmatic
 *  agents)                                agents)
 *
 * Before this file, only WebMcp.astro had the matrix, delivered solely through
 * `navigator.modelContext` - an API no fetching agent can reach. An agent that
 * asked daysurface.com how to get started was served 272KB of HTML with the
 * install links buried in a JS dropdown, and concluded the site had none.
 *
 * `displayName` is deliberately `site.name`, not `site.serverName`: it is the
 * label a client shows in its install dialog (Claude's `connectorName`), and
 * the visible-UI value is the one that was click-tested. WebMcp.astro used to
 * omit it and hand agents a lowercased `daysurface` instead.
 */
import { connect, type InstallTarget } from "./connect";
import { site } from "./site";
import { deepLink } from "../../utils/deeplink";

/**
 * How much work the visitor still has to do, which is the axis both the page
 * and the agent-facing markdown group by. Derived - never hand-set - so a
 * target that changes `method`/`prefills` in connect.ts moves group by itself.
 *
 * `dialog-only` is its own bucket rather than folded into `one-click` because
 * ChatGPT's link opens an EMPTY dialog. connect.ts and deeplink.ts both warn
 * at length that calling it one-click is worse than shipping no link at all:
 * it tells someone the job is done while the form in front of them is blank.
 */
export type InstallEffort = "one-click" | "command" | "dialog-only" | "prompt" | "manual";

/** A single client's install path, flattened for JSON and markdown alike. */
export interface InstallClient {
  id: string;
  name: string;
  method: InstallTarget["method"];
  effort: InstallEffort;
  /** Deep link, when the client has a URL scheme. Null for prompt/manual targets. */
  install_url: string | null;
  /** Deep links only: does the link arrive with the fields filled in? */
  prefills: boolean | null;
  /** Click-path: the whole job for manual targets, the fallback for deep links. */
  steps: string[] | null;
  /** Prompt targets: the text to paste (or run - see `setup_kind`). */
  setup_prompt: string | null;
  setup_kind: InstallTarget["setupKind"] | null;
  note: string | null;
}

/** Classify a target by the work left after the visitor clicks. */
function effortOf(t: InstallTarget): InstallEffort {
  if (t.method === "deeplink") return t.prefills === false ? "dialog-only" : "one-click";
  if (t.method === "prompt") return t.setupKind === "command" ? "command" : "prompt";
  return "manual";
}

/** Build the full matrix. Pure and build-time; safe to call from any consumer. */
export function buildInstallMatrix(): InstallClient[] {
  return connect.targets.map((t) => ({
    id: t.id,
    name: t.name,
    method: t.method,
    effort: effortOf(t),
    install_url:
      t.method === "deeplink"
        ? deepLink(t.id, site.mcpUrl, site.serverName, site.name)
        : null,
    prefills: t.method === "deeplink" ? t.prefills !== false : null,
    steps: t.steps ?? null,
    setup_prompt: t.setupPrompt ?? null,
    setup_kind: t.setupKind ?? null,
    note: t.note ?? null,
  }));
}

/**
 * Group headings, in render order. The heading has to survive being read on its
 * own by an agent that never sees the surrounding page, so each states the
 * remaining work rather than naming the mechanism ("One click" beats "Deep
 * links"). An empty group is dropped by the consumers, so this list can name
 * efforts no current target uses.
 */
export const installGroups: readonly { effort: InstallEffort; heading: string }[] = [
  { effort: "one-click", heading: "One click - the link fills in the name and URL" },
  { effort: "command", heading: "One command" },
  { effort: "dialog-only", heading: "Opens the dialog, but you still paste the URL" },
  { effort: "prompt", heading: "Paste this prompt into the client" },
  { effort: "manual", heading: "Add it by hand" },
];

/** The matrix bucketed by effort, in `installGroups` order, empties dropped. */
export function groupedInstallMatrix(): {
  effort: InstallEffort;
  heading: string;
  clients: InstallClient[];
}[] {
  const all = buildInstallMatrix();
  return installGroups
    .map((g) => ({ ...g, clients: all.filter((c) => c.effort === g.effort) }))
    .filter((g) => g.clients.length > 0);
}

/**
 * Copy for the connect page and its markdown twin.
 *
 * `noAccount` leads every surface and is the single most load-bearing sentence
 * on the site for agents: an agent sent to find a signup flow needs the premise
 * refuted in the first thing it reads, before it starts probing routes. The
 * words "sign up", "account" and "register" appear here on purpose - they were
 * absent from the entire site, so an agent searching for them found nothing and
 * fell back to the GitHub README.
 */
export const connectPage = {
  title: `Connect ${site.name}`,
  noAccount: `There is no ${site.name} account to create, no signup form, and nothing to install. ${site.name} is a remote MCP server: you add one URL to an MCP client and sign in to Google inside that client.`,
  agentShortcut: `If you are an agent that can add MCP servers, the endpoint above is the whole job - add it and call \`tools/list\`. Otherwise pick your client below.`,
  facts: [
    { label: "Endpoint", value: site.mcpUrl },
    { label: "Name", value: site.serverName },
    { label: "Transport", value: "streamable HTTP (remote, not stdio)" },
    { label: "Auth", value: "OAuth in the browser - no API key to paste" },
  ],
} as const;

/**
 * Paths that answer the connect question, all serving the same page.
 *
 * An agent asked to "create an account" probes these before it reads anything,
 * and the SPA fallback used to answer every one of them with a 200 and the
 * homepage - indistinguishable from a real page, so the agent concluded the
 * site was client-rendered. They now resolve to the connect content itself,
 * canonicalised to `/connect` and marked noindex so only one URL is indexed.
 */
export const connectAliases: readonly string[] = [
  "signup",
  "sign-up",
  "register",
  "login",
  "sign-in",
  "account",
  "get-started",
  "start",
  "install",
  "setup",
  "onboarding",
];
