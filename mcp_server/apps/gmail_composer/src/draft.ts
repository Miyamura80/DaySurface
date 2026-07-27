import type { Draft, DraftAttachment, Thread } from "./types";

// Unwrap an MCP CallToolResult to its structured payload: prefer
// `structuredContent`, else parse a JSON `TextContent` item. Returns null when
// neither is present - never the raw envelope - so a malformed result can't
// masquerade as a draft/thread and content-only JSON results aren't dropped.
//
// Keep in sync with gmail_inbox/src/helpers.ts:extractStructuredContent - the
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

// Pull the draft's existing attachments (each with a stable attachment_id) off a
// GmailDraft payload. Only files with an id are usable: the id is what a save
// passes back as a reference to preserve the file in the whole-set replace.
export function extractAttachments(raw: unknown): DraftAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: DraftAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const a = item as Record<string, unknown>;
    const id = a["attachment_id"];
    if (typeof id !== "string" || id.length === 0) continue;
    // GmailDraftAttachment emits both `size` and the computed `size_bytes`;
    // prefer the public `size_bytes` name and fall back to `size`.
    const rawSize = a["size_bytes"] ?? a["size"];
    out.push({
      attachment_id: id,
      filename:
        typeof a["filename"] === "string" ? (a["filename"] as string) : "(file)",
      mime_type:
        typeof a["mime_type"] === "string" ? (a["mime_type"] as string) : undefined,
      size: typeof rawSize === "number" ? rawSize : undefined,
    });
  }
  return out;
}

export function extractDraft(raw: unknown): Draft | null {
  const data = extractStructuredContent<Record<string, unknown>>(raw);
  if (!data) return null;
  const draftId = data["draft_id"];
  if (typeof draftId !== "string") return null;
  return {
    draft_id: draftId,
    from: typeof data["from"] === "string" ? (data["from"] as string) : undefined,
    to: typeof data["to"] === "string" ? (data["to"] as string) : undefined,
    cc: typeof data["cc"] === "string" ? (data["cc"] as string) : undefined,
    bcc: typeof data["bcc"] === "string" ? (data["bcc"] as string) : undefined,
    subject:
      typeof data["subject"] === "string" ? (data["subject"] as string) : undefined,
    body: typeof data["body"] === "string" ? (data["body"] as string) : undefined,
    thread_id:
      typeof data["thread_id"] === "string"
        ? (data["thread_id"] as string)
        : undefined,
    attachments: extractAttachments(data["attachments"]),
  };
}

export function fieldsEqual(a: Draft, b: Draft): boolean {
  return (
    a.to === b.to &&
    a.cc === b.cc &&
    a.bcc === b.bcc &&
    a.subject === b.subject &&
    a.body === b.body
  );
}

export function extractThread(raw: unknown): Thread | null {
  const data = extractStructuredContent<Record<string, unknown>>(raw);
  if (!data) return null;
  if (!Array.isArray((data as { messages?: unknown }).messages)) return null;
  return data as unknown as Thread;
}

// Canonical enumeration of the user-editable draft fields, normalized to the
// empty-string wire form the server tools expect. persistDraft, onSend, and
// sentContextText must all agree on this set - a new Draft field is added
// here, not at each call site.
export function draftFields(draft: Draft): {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
} {
  return {
    to: draft.to ?? "",
    cc: draft.cc ?? "",
    bcc: draft.bcc ?? "",
    subject: draft.subject ?? "",
    body: draft.body ?? "",
  };
}
