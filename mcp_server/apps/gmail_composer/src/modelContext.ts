import type { Draft } from "./types";
import { draftFields } from "./draft";

// All composer tools are `visibility: ["app"]` and app-initiated tool calls
// never enter the model's context, so without an explicit push the agent
// never learns the user clicked Send/Discard - or that they edited fields
// after the agent's last update. These build the text pushed via
// `updateModelContext` on those two transitions (and only those two: wiring
// this into the debounced autosave would spam the context on every pause in
// typing).
export function sentContextText(draft: Draft, messageId: string): string {
  const f = draftFields(draft);
  const fields = [
    `to: ${f.to}`,
    ...(f.cc ? [`cc: ${f.cc}`] : []),
    ...(f.bcc ? [`bcc: ${f.bcc}`] : []),
    `subject: ${f.subject}`,
    ...(messageId ? [`message_id: ${messageId}`] : []),
  ];
  return [
    "The user clicked Send in the email composer. The email below has been sent.",
    "This is the final sent version; it supersedes any earlier draft content in this conversation (the user may have edited fields after the last agent update).",
    "---",
    ...fields,
    "---",
    "",
    f.body,
  ].join("\n");
}

export function discardContextText(draft: Draft): string {
  return (
    `The user clicked Discard in the email composer. Draft ${draft.draft_id} ` +
    "was deleted without being sent; it no longer exists and must not be " +
    "referenced or sent."
  );
}
