import type {
  ComposerDraft,
  CuratedThread,
  CurationRecord,
  DraftAttachment,
  ExistingAttachment,
  FileAttachment,
  LabelChip,
  McpAppLike,
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

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Build the composer's `attachments` argument for save_draft / send.
//
// The backend replaces the draft's entire attachment set when `attachments`
// is a non-empty list, preserving existing files that are re-listed by
// `{attachment_id}` reference, and treats an OMITTED argument as
// "preserve every existing file" (see gmail_update_draft).
//
// `changed` is whether the user has added or removed an attachment in this
// composer session:
//   - not changed  -> return undefined so the caller omits the argument and
//     the backend keeps every file untouched (the common text-only-edit path,
//     and the only safe way to preserve files that carry no referenceable id).
//   - changed      -> return the explicit desired set: `{attachment_id}` refs
//     for the existing files followed by the new uploads. Sending this on
//     every save/send after a change is what lets a removal actually take
//     effect: once a new upload has been persisted, omitting the argument
//     would "preserve" it server-side and the removal would be lost.
export function buildAttachmentsPayload(
  newUploads: FileAttachment[],
  existing: ExistingAttachment[],
  changed: boolean,
): Array<Record<string, unknown>> | undefined {
  if (!changed) return undefined;
  const refs = existing
    .filter((a) => a.attachment_id)
    .map((a) => ({ attachment_id: a.attachment_id }));
  const uploads = newUploads.map(({ filename, mime_type, data_base64 }) => ({
    filename,
    mime_type,
    data_base64,
  }));
  return [...refs, ...uploads];
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
