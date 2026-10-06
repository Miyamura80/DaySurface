// Tells the host's model what the user did in the composer.
//
// Send and discard go through app-only tools the model never sees, so without
// this the agent keeps offering to edit (or resend) a draft that is already
// gone. `ui/update-model-context` reaches the model on its next turn without
// starting one. The host keeps only the latest update, so each call re-sends a
// short log of recent actions rather than just the newest. The log is per App
// instance, i.e. per rendered iframe: each view reports what happened in it.
import type { ComposerDraft, McpAppLike } from "./types";

export type ComposerAction = {
  kind: "sent" | "discarded";
  draft_id: string;
  thread_id?: string;
  message_id?: string;
  to?: string;
  subject?: string;
  at: string;
};

const MAX_ACTIONS = 5;
const logs = new WeakMap<McpAppLike, ComposerAction[]>();

function formatAction(a: ComposerAction): string {
  const what = a.kind === "sent"
    ? `SENT draft ${a.draft_id} as message ${a.message_id ?? "?"}`
    : `DISCARDED draft ${a.draft_id}`;
  const thread = a.thread_id ? ` on thread ${a.thread_id}` : "";
  const subject = a.subject ? `, subject "${a.subject}"` : "";
  const to = a.kind === "sent" && a.to ? `, to ${a.to}` : "";
  return `- ${a.at}: ${what}${thread}${subject}${to}.`;
}

export function composerContextText(actions: ComposerAction[]): string {
  return [
    "The user did this themselves in the DaySurface email composer (oldest first):",
    ...actions.map(formatAction),
    "Those drafts no longer exist. Do not edit, resend, or recreate them, and don't offer to.",
  ].join("\n");
}

/** Record a composer action and push the recent log to the model (best-effort). */
export async function reportComposerAction(
  app: McpAppLike,
  kind: ComposerAction["kind"],
  draft: ComposerDraft,
  sent?: { message_id?: string; thread_id?: string },
): Promise<void> {
  if (!app.updateModelContext || !app.getHostCapabilities?.()?.updateModelContext) return;
  const action: ComposerAction = {
    kind,
    draft_id: draft.draft_id,
    // A new (non-reply) draft only learns its thread once Gmail sends it.
    thread_id: sent?.thread_id || draft.thread_id,
    message_id: sent?.message_id,
    to: draft.to,
    subject: draft.subject,
    at: new Date().toISOString(),
  };
  const actions = [...(logs.get(app) ?? []), action].slice(-MAX_ACTIONS);
  logs.set(app, actions);
  try {
    await app.updateModelContext({
      content: [{ type: "text", text: composerContextText(actions) }],
      structuredContent: { composer_actions: actions },
    });
  } catch {
    // The action itself already succeeded; a host that rejects the update only
    // leaves the model as unaware as it was before.
  }
}
