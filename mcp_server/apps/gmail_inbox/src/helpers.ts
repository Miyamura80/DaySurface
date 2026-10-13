import type {
  ComposerDraft,
  CuratedThread,
  CurationRecord,
  DraftAttachment,
  LabelChip,
  McpAppLike,
  SendResult,
  ToolCallResult,
} from "./types";

export type ToolResultBuffer = {
  /**
   * Take over live `ontoolresult` delivery with `handler`, first replaying (oldest
   * first) any results the host delivered before this call.
   */
  drainInto: (handler: (raw: unknown) => void) => void;
};

/**
 * Capture `ontoolresult` deliveries from the earliest possible moment.
 *
 * The host can deliver the initiating tool result before the React component's
 * mount effect runs and assigns its `ontoolresult` handler; that result would
 * otherwise be lost, and for a thread-open the app would fall back to a curated-
 * inbox refresh instead of showing the requested thread. Installing this buffer
 * in the entry point (before `connect()`/render) means the result is captured no
 * matter when it arrives; the component then calls `drainInto(handler)` on mount
 * to replay it through its real handler and take over live delivery.
 *
 * Replay-then-assign is gapless: there is no `await` between the two, so no
 * postMessage-driven `ontoolresult` can interleave and be dropped.
 */
export function bufferToolResults(
  app: Pick<McpAppLike, "ontoolresult">,
): ToolResultBuffer {
  const pending: unknown[] = [];
  app.ontoolresult = (raw) => {
    pending.push(raw);
  };
  return {
    drainInto(handler) {
      const buffered = pending.splice(0, pending.length);
      for (const raw of buffered) handler(raw);
      app.ontoolresult = handler;
    },
  };
}

const BUCKET_CHIP: Record<string, LabelChip> = {
  needs_reply: { name: "Needs reply", bg_color: "#fce8e6", text_color: "#c5221f" },
  waiting_on: { name: "Waiting on", bg_color: "#fef7e0", text_color: "#b06000" },
  fyi: { name: "FYI", bg_color: "#e8f0fe", text_color: "#1a73e8" },
  noise: { name: "Noise", bg_color: "#f1f3f4", text_color: "#5f6368" },
};

// Map banked ledger records into the row shape the list already renders. The
// ledger stores a summary + bucket rather than subject/sender, so the summary
// becomes the primary line and the bucket + freshness become label chips.
export function curationToThreads(records: CurationRecord[]): CuratedThread[] {
  return records.map((r) => {
    const labels: LabelChip[] = [];
    const chip = r.bucket ? BUCKET_CHIP[r.bucket] : undefined;
    if (chip) labels.push(chip);
    if (r.ledger_status === "stale")
      labels.push({ name: "Stale", bg_color: "#fef7e0", text_color: "#b06000" });
    const action =
      r.suggested_action && r.suggested_action !== "none"
        ? r.suggested_action.replace(/_/g, " ")
        : undefined;
    return {
      thread_id: r.thread_id,
      subject: r.summary ?? r.bucket ?? "(curated thread)",
      from: action ? `Suggested: ${action}` : undefined,
      importance_score: r.importance ?? 0,
      reasons: [],
      labels,
      has_draft: !!r.draft_id,
      draft_id: r.draft_id ?? undefined,
    };
  });
}

export function extractDraft(raw: unknown): ComposerDraft | null {
  const data = extractStructuredContent<Record<string, unknown>>(raw);
  if (!data) return null;
  const draftId = data["draft_id"];
  if (typeof draftId !== "string") return null;
  return {
    draft_id: draftId,
    to: typeof data["to"] === "string" ? data["to"] as string : undefined,
    cc: typeof data["cc"] === "string" ? data["cc"] as string : undefined,
    bcc: typeof data["bcc"] === "string" ? data["bcc"] as string : undefined,
    subject: typeof data["subject"] === "string" ? data["subject"] as string : undefined,
    body: typeof data["body"] === "string" ? data["body"] as string : undefined,
    thread_id: typeof data["thread_id"] === "string" ? data["thread_id"] as string : undefined,
    attachments: Array.isArray(data["attachments"]) ? data["attachments"] as DraftAttachment[] : undefined,
  };
}

export function draftFieldsEqual(a: ComposerDraft, b: ComposerDraft): boolean {
  return a.to === b.to && a.cc === b.cc && a.bcc === b.bcc && a.subject === b.subject && a.body === b.body;
}

export function splitHtmlAtQuote(html: string): { main: string; quoted: string | null } {
  const markers = ['<div class="gmail_quote"', '<blockquote class="gmail_quote"', '<div class=3D"gmail_quote"'];
  for (const marker of markers) {
    const idx = html.indexOf(marker);
    if (idx > 0) return { main: html.slice(0, idx), quoted: html.slice(idx) };
  }
  const onWroteRe = /(<br\s*\/?>[\s\S]{0,20}?On\s.{10,80}\s+wrote:\s*<br\s*\/?>)/i;
  const m = onWroteRe.exec(html);
  if (m && m.index > 50) return { main: html.slice(0, m.index), quoted: html.slice(m.index) };
  return { main: html, quoted: null };
}

export function splitTextAtQuote(text: string): { main: string; quoted: string | null } {
  const lines = text.split("\n");
  const onWroteRe = /^On .{10,80} wrote:\s*$/;
  for (let i = 0; i < lines.length; i++) {
    if (onWroteRe.test(lines[i]) && i > 0)
      return { main: lines.slice(0, i).join("\n"), quoted: lines.slice(i).join("\n") };
  }
  let firstQ = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith(">")) { if (firstQ === -1) firstQ = i; } else if (firstQ !== -1) break;
  }
  if (firstQ > 0 && lines.length - firstQ >= 3)
    return { main: lines.slice(0, firstQ).join("\n"), quoted: lines.slice(firstQ).join("\n") };
  return { main: text, quoted: null };
}

// Unwrap an MCP CallToolResult to its structured payload: prefer
// `structuredContent`, else parse a JSON `TextContent` item. Returns null when
// neither is present - never the raw envelope - so a malformed result can't
// masquerade as a draft/thread and content-only JSON results aren't dropped.
//
// Keep in sync with gmail_composer/src/draft.ts:extractStructuredContent - the
// apps are isolated bun packages and can't share this; #170 tracks folding the
// per-app boilerplate into a shared source once that's designed.
export function extractStructuredContent<T>(raw: unknown): T | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.structuredContent && typeof obj.structuredContent === "object") {
    return obj.structuredContent as T;
  }
  if (Array.isArray(obj.content)) {
    for (const item of obj.content) {
      if (!item || typeof item !== "object") continue;
      // Only a real MCP TextContent block qualifies: a `type: "text"`
      // discriminator with a string `text`. Without this an image/resource/other
      // block that happens to carry a `text` field could smuggle JSON through.
      const candidate = item as { type?: unknown; text?: unknown };
      if (candidate.type !== "text" || typeof candidate.text !== "string") continue;
      try {
        const parsed = JSON.parse(candidate.text);
        if (parsed && typeof parsed === "object") return parsed as T;
      } catch { /* not JSON text content */ }
    }
  }
  return null;
}

// The first text block of a tool-level failure, or null.
function toolErrorText(raw: unknown): string | null {
  for (const item of (raw as ToolCallResult | null)?.content ?? []) {
    const c = item as { type?: unknown; text?: unknown } | null;
    if (c?.type === "text" && typeof c.text === "string" && c.text) return c.text;
  }
  return null;
}

const GONE_UNRECORDED =
  "This draft is no longer in Gmail, and the server has no record of sending it. Check your Sent folder.";

// After a lost send reply, ask the server what became of the draft. Its own
// sent record is the only proof of a send: Gmail also deletes a discarded
// draft, so "gone" alone is not "sent". Returns the recorded send, or throws
// GONE_UNRECORDED when the draft is gone without one, or `err` when the draft
// still exists or the check fails too, so the composer stays editable.
async function resolveLostSend(app: McpAppLike, draftId: string, err: unknown): Promise<SendResult> {
  let status: { draft_exists?: boolean; sent?: SendResult | null } | null;
  try {
    const raw = await app.callServerTool({
      name: "gmail_composer.send_status",
      arguments: { draft_id: draftId },
    });
    status = extractStructuredContent(raw);
  } catch {
    throw err;
  }
  if (status?.sent?.message_id) return status.sent;
  if (status?.draft_exists === false) throw new Error(GONE_UNRECORDED);
  throw err;
}

// Send the draft and return Gmail's confirmation, or throw the error to show.
// "Sent" is terminal, so a false positive is unrecoverable: a definite server
// failure (isError) is shown as-is. Only a lost reply (the call rejected, or
// resolved without a message_id) asks the server for its sent record.
export async function confirmSend(
  app: McpAppLike,
  args: Record<string, unknown> & { draft_id: string },
): Promise<SendResult> {
  let raw: unknown;
  try {
    raw = await app.callServerTool({ name: "gmail_composer.send", arguments: args });
  } catch (err) {
    return resolveLostSend(app, args.draft_id, err);
  }
  const result = (raw ?? {}) as ToolCallResult;
  if (result.isError) throw new Error(toolErrorText(result) ?? "the send failed");
  const sent = extractStructuredContent<SendResult>(raw);
  if (sent?.message_id) return sent;
  return resolveLostSend(app, args.draft_id, new Error("the server did not confirm the send"));
}

// Call a write tool and return its result, throwing the server's own text when
// it reports a tool-level failure (callServerTool resolves on isError too).
export async function callWriteTool(
  app: McpAppLike,
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const raw = await app.callServerTool({ name, arguments: args });
  if ((raw as ToolCallResult | null)?.isError) throw new Error(toolErrorText(raw) ?? `${name} failed`);
  return raw;
}

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function isPreviewable(mime?: string): boolean {
  if (!mime) return false;
  return mime === "application/pdf" || mime.startsWith("image/");
}

export function base64ToBlobUrl(b64: string, mime: string): string {
  const raw = atob(b64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return URL.createObjectURL(new Blob([arr], { type: mime }));
}

export function relativeTime(iso: string | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const ageMs = Date.now() - dt.getTime();
  const mins = Math.round(ageMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return dt.toLocaleDateString();
}

export function formatDate(iso: string | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString();
}
