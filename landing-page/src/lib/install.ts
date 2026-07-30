/**
 * The client install matrix - one flat, serializable record per supported MCP
 * client, derived at build time from `config/landing/connect.ts` +
 * `utils/deeplink.ts`.
 *
 * This is derivation, not config, which is why it lives in `lib/` rather than
 * under `config/landing/`: nothing here is edited to change the site, and
 * everything here is recomputed from the config that is.
 *
 *   connect.ts + deeplink.ts
 *            │
 *            ▼
 *   installMatrix  ──  effortOf() ── effortMeta
 *            │
 *   ┌────────┼──────────┬───────────┬────────────┐
 *   ▼        ▼          ▼           ▼            ▼
 * WebMcp  Connect-    Connect-   connect.md   install.json
 * (browser Widget     Body       + llms.txt   (programmatic
 *  agents) (picker)   (/connect) (LLM agents)  agents)
 *
 * Before this existed, only WebMcp.astro had the matrix, delivered solely
 * through `navigator.modelContext` - an API no fetching agent can reach. An
 * agent that asked daysurface.com how to get started was served 272KB of HTML
 * with the install links buried in a JS dropdown, and concluded it had none.
 *
 * `displayName` is deliberately `site.name`, not `site.serverName`: it is the
 * label a client shows in its install dialog (Claude's `connectorName`), and
 * the visible-UI value is the one that was click-tested. WebMcp.astro used to
 * omit it and hand agents a lowercased `daysurface` instead.
 */
import { connect, site, type InstallTarget } from "../config/landing";
import { deepLink } from "../utils/deeplink";

/**
 * How much work the visitor still has to do. Derived - never hand-set - so a
 * target that changes `method`/`prefills` in connect.ts reclassifies itself.
 *
 * This is the site's single answer to "is this one click?", and every renderer
 * must ask it rather than re-deriving from `method`/`prefills`/`setupKind`. The
 * rule that makes it necessary: ChatGPT's link opens an EMPTY dialog, and
 * connect.ts and deeplink.ts both warn at length that calling it one-click is
 * worse than shipping no link at all - it tells someone the job is done while
 * the form in front of them is blank. That rule is expressed here, once.
 */
export type InstallEffort = "one-click" | "command" | "dialog-only" | "prompt";

/**
 * Every string that varies by effort, in one table.
 *
 * Five surfaces render this matrix. When each derived its own labels, the
 * dialog-only rule was written by hand in eleven places and had already drifted
 * (`"Full click-path"` vs `"Then, in the dialog the link opens"` for the same
 * concept). Adding a field here is how a renderer gets a new label.
 */
export const effortMeta: Record<
  InstallEffort,
  {
    /** Section heading on /connect and in connect.md. States the work left. */
    heading: string;
    /** Short badge in the client picker dropdown. */
    badge: string;
    /**
     * Label for the steps disclosure. Carries whether `steps` are a fallback
     * for a link that failed or the REMAINING job - getting that backwards
     * tells an agent to navigate to a dialog already open in front of it.
     */
    stepsLabel: string;
    /** One-line "how" for the quick-reference list in llms-full.txt. */
    shortHow: string;
  }
> = {
  "one-click": {
    heading: "One click - the link fills in the name and URL",
    badge: "1-click",
    stepsLabel: "If the link does not work",
    shortHow: "one-click install (deep link fills in the name and URL).",
  },
  command: {
    heading: "One command",
    badge: "command",
    stepsLabel: "If the command does not work",
    shortHow: "run the command below.",
  },
  "dialog-only": {
    heading: "Opens the dialog, but you still paste the URL",
    badge: "shortcut",
    stepsLabel: "Then, in the dialog the link opens",
    shortHow: "install link opens the setup dialog, but the fields come up empty - paste the URL there.",
  },
  prompt: {
    heading: "Paste this prompt into the client",
    badge: "prompt",
    stepsLabel: "Or add it by hand",
    shortHow: "paste the setup prompt into the client.",
  },
};

/** Render order for the effort groups. */
const EFFORT_ORDER: readonly InstallEffort[] = [
  "one-click",
  "command",
  "dialog-only",
  "prompt",
];

/** A single client's install path, flattened for JSON and markdown alike. */
export interface InstallClient {
  id: string;
  name: string;
  method: InstallTarget["method"];
  effort: InstallEffort;
  /** Deep link, when the client has a URL scheme. Null for prompt targets. */
  install_url: string | null;
  /** Deep links only: does the link arrive with the fields filled in? */
  prefills: boolean | null;
  /** Click-path: the remaining job or a fallback - see `effortMeta.stepsLabel`. */
  steps: string[] | null;
  /** Prompt targets: the text to paste (or run - see `setup_kind`). */
  setup_prompt: string | null;
  setup_kind: InstallTarget["setupKind"] | null;
  note: string | null;
}

/**
 * Classify a target by the work left after the click.
 *
 * `manual` targets (no install URL, no prompt - just a click-path) currently
 * do not exist; connect.ts keeps the method for the next host that ships no URL
 * scheme at all. They group with `prompt`, whose heading and steps handling
 * already fit "you configure this yourself".
 */
function effortOf(t: InstallTarget): InstallEffort {
  if (t.method === "deeplink") return t.prefills === false ? "dialog-only" : "one-click";
  if (t.setupKind === "command") return "command";
  return "prompt";
}

/** The full matrix. Computed once at module load - pure and argument-less. */
export const installMatrix: readonly InstallClient[] = connect.targets.map((t) => ({
  id: t.id,
  name: t.name,
  method: t.method,
  effort: effortOf(t),
  install_url:
    t.method === "deeplink" ? deepLink(t.id, site.mcpUrl, site.serverName, site.name) : null,
  prefills: t.method === "deeplink" ? t.prefills !== false : null,
  steps: t.steps ?? null,
  setup_prompt: t.setupPrompt ?? null,
  setup_kind: t.setupKind ?? null,
  note: t.note ?? null,
}));

/**
 * The matrix with each client's logo, for the UI surfaces.
 *
 * Kept off `InstallClient` so the agent contract (install.json) stays free of
 * a site-relative asset path, but joined here rather than at each call site -
 * ConnectWidget used to rebuild this join by hand from `connect.targets`.
 */
export const installClientsForUi: readonly (InstallClient & { logo: string })[] =
  installMatrix.map((c, i) => ({ ...c, logo: connect.targets[i].logo }));

export interface InstallGroup {
  effort: InstallEffort;
  heading: string;
  /**
   * The setup prompt every client in this group shares, or null when they
   * differ. Computed here so both the markdown and the HTML page can print it
   * once above the list: four prompt targets share one ~450-character prompt,
   * and repeating it per client inflated connect.md by a third and /connect by
   * ~1.5KB - on two surfaces whose whole purpose is surviving truncation.
   */
  sharedPrompt: string | null;
  clients: InstallClient[];
}

/** The matrix bucketed by effort, in render order, empty groups dropped. */
export function groupedInstallMatrix(): InstallGroup[] {
  return EFFORT_ORDER.map((effort) => {
    const clients = installMatrix.filter((c) => c.effort === effort);
    const first = clients[0]?.setup_prompt ?? null;
    const shared =
      first && clients.length > 1 && clients.every((c) => c.setup_prompt === first)
        ? first
        : null;
    return {
      effort,
      heading: effortMeta[effort].heading,
      sharedPrompt: shared,
      clients,
    };
  }).filter((g) => g.clients.length > 0);
}
