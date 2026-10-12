import { useEffect, useRef, useState } from "react";
import type React from "react";
import type { ComposerDraft, DraftAttachment, McpAppLike } from "./types";
import {
  buildAttachmentWrite,
  isPending,
  reconcileSlots,
  slotsFromDraft,
  type AttachmentSlot,
  type AttachmentWrite,
} from "./attachmentSlots";
import { base64ToBlobUrl, extractStructuredContent, formatFileSize } from "./helpers";
import type { PreviewData } from "./AttachmentPreview";

// Cap a single attachment just under the server's limit: AttachmentInput caps
// base64 at 34M chars (~25.5 MB decoded, models/gmail.py), and Gmail's ceiling
// is 25 MB for the WHOLE message. 25 MB (decimal) keeps a drop safely inside
// the base64 validator, so the client guard actually prevents a wasted read and
// round-trip instead of surfacing a raw validation error 800ms later.
export const MAX_ATTACHMENT_BYTES = 25_000_000;
// Spelled out rather than run through formatFileSize: that helper divides by
// 1024^2 while labelling the result "MB", which renders this decimal cap as
// "23.8 MB" - a limit no user would recognise as the documented 25 MB.
const MAX_ATTACHMENT_LABEL = "25 MB";

/** A file the user picked that never left the browser, with the reason. */
export type RejectedAttachment = { filename: string; reason: string };

/**
 * Attachment state and the preview modal for the inline composer. Every file,
 * confirmed or still pending upload, is one slot in `slots`; the composer's
 * write queue reads `slotsRef` when a write runs and folds each reply back in
 * with `reconcile`, so a pick is uploaded once and then kept by reference.
 * `onChange` fires after a pick or removal so the composer can persist it.
 */
export function useComposerAttachments(
  draft: ComposerDraft,
  mcpApp: McpAppLike,
  onChange: () => void,
) {
  const keyRef = useRef(0);
  const nextKey = () => ++keyRef.current;
  const [slots, setSlotsState] = useState<AttachmentSlot[]>(
    () => slotsFromDraft(draft.attachments || [], nextKey),
  );
  // Mirrors `slots` synchronously: a queued write runs after renders it did not
  // see, and must build on the latest set rather than a stale closure.
  const slotsRef = useRef(slots);
  const setSlots = (next: AttachmentSlot[]) => {
    slotsRef.current = next;
    setSlotsState(next);
  };
  const [rejected, setRejected] = useState<RejectedAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // True once the user adds or removes an attachment. Until then, save/send
  // omit the `attachments` argument so the backend preserves every existing
  // file; after a change we send the explicit desired set so a removal sticks.
  const attachmentsDirtyRef = useRef(false);
  // Bytes accepted by a preflight whose FileReader has not resolved yet. They
  // are not in `slots` until onload runs, so a second pick made during that
  // window would not see them and the cumulative check would undercount -
  // exactly the fail-open the check exists to prevent.
  const pendingBytesRef = useRef(0);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);
  const previewSeqRef = useRef(0);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  // Sync confirmed files when the draft prop updates (refresh or agent edit),
  // keeping picks that have not been uploaded yet. An absent key means "the
  // update did not carry attachments", so keep what we have; an empty array
  // means "this draft has none", which must clear the confirmed list.
  const firstSyncRef = useRef(true);
  useEffect(() => {
    if (firstSyncRef.current) { firstSyncRef.current = false; return; }
    if (!draft.attachments) return;
    setSlots([
      ...slotsFromDraft(draft.attachments, nextKey),
      ...slotsRef.current.filter(isPending),
    ]);
  }, [draft.attachments]);

  const buildWrite = (): AttachmentWrite =>
    buildAttachmentWrite(slotsRef.current, attachmentsDirtyRef.current);

  const reconcile = (sent: AttachmentSlot[], server: DraftAttachment[] | undefined) => {
    if (!Array.isArray(server)) return;
    setSlots(reconcileSlots(slotsRef.current, sent, server, nextKey));
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const chosen = Array.from(files);
    e.target.value = "";
    if (chosen.length === 0) return;

    // Classify before reading anything. An empty file would send an empty
    // data_base64 the server rejects, and an oversized one would be read into
    // the iframe in full only to fail validation - both are inline errors here.
    const rejects: RejectedAttachment[] = [];
    const accepted: File[] = [];
    for (const f of chosen) {
      if (f.size === 0) {
        rejects.push({ filename: f.name, reason: "Empty file (0 bytes)" });
      } else if (f.size > MAX_ATTACHMENT_BYTES) {
        rejects.push({
          filename: f.name,
          reason: `Too large (${formatFileSize(f.size)}); ${MAX_ATTACHMENT_LABEL} max`,
        });
      } else {
        accepted.push(f);
      }
    }

    // Cumulative check against everything already on the draft, so a batch that
    // individually passes but collectively blows Gmail's whole-message limit is
    // refused before hundreds of MB of base64 are read. Fail CLOSED on an
    // unknown size: a confirmed file's size is optional, and coalescing a
    // missing one to 0 would undercount and let an over-limit batch through.
    // Each file is exactly one slot, so nothing is counted twice.
    const committedBytes =
      slotsRef.current.reduce((sum, a) => sum + (a.size ?? MAX_ATTACHMENT_BYTES), 0) +
      pendingBytesRef.current;
    const incomingBytes = accepted.reduce((s, f) => s + f.size, 0);
    const fits = committedBytes + incomingBytes <= MAX_ATTACHMENT_BYTES;
    if (!fits) {
      for (const f of accepted) {
        rejects.push({
          filename: f.name,
          reason: `Message would exceed ${MAX_ATTACHMENT_LABEL}`,
        });
      }
    }
    if (rejects.length) setRejected((prev) => [...prev, ...rejects]);
    if (!fits || accepted.length === 0) return;

    // Claim the bytes synchronously, before any read starts, so a pick made
    // while these are still reading sees them in the cumulative check above.
    pendingBytesRef.current += incomingBytes;

    accepted.forEach((file) => {
      const reader = new FileReader();
      const release = () => { pendingBytesRef.current -= file.size; };
      reader.onload = () => {
        release();
        const result = reader.result as string;
        const base64 = result.split(",")[1] || "";
        attachmentsDirtyRef.current = true;
        setSlots([
          ...slotsRef.current,
          {
            key: nextKey(),
            filename: file.name,
            mime_type: file.type || "application/octet-stream",
            data_base64: base64,
            size: file.size,
          },
        ]);
        onChange();
      };
      // Release on failure too, or an unreadable file would hold its bytes
      // against the cap for the rest of the session.
      reader.onerror = () => {
        release();
        setRejected((prev) => [...prev, { filename: file.name, reason: "Could not read file" }]);
      };
      reader.readAsDataURL(file);
    });
  };

  const dismissRejected = (index: number) => {
    setRejected((prev) => prev.filter((_, i) => i !== index));
  };

  const removeAttachment = (key: number) => {
    attachmentsDirtyRef.current = true;
    setSlots(slotsRef.current.filter((s) => s.key !== key));
    onChange();
  };

  const closePreview = () => {
    previewSeqRef.current++;
    if (previewBlobRef.current) {
      URL.revokeObjectURL(previewBlobRef.current);
      previewBlobRef.current = null;
    }
    setPreviewData(null);
    setPreviewLoading(false);
  };

  const showPreview = (b64: string, mime: string, filename: string) => {
    previewSeqRef.current++;
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
    const url = base64ToBlobUrl(b64, mime);
    previewBlobRef.current = url;
    setPreviewData({ url, filename, mime_type: mime });
  };

  const previewAttachment = async (att: AttachmentSlot) => {
    if (att.data_base64 !== undefined) {
      showPreview(att.data_base64, att.mime_type || "application/octet-stream", att.filename);
      return;
    }
    if (!att.attachment_id || !att.message_id) return;
    const seq = ++previewSeqRef.current;
    setPreviewLoading(true);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.get_attachment",
        arguments: { message_id: att.message_id, attachment_id: att.attachment_id },
      });
      if (seq !== previewSeqRef.current) return;
      const parsed = extractStructuredContent<{ data_base64?: string }>(raw);
      const b64 = parsed?.data_base64;
      if (b64) {
        showPreview(b64, att.mime_type || "application/octet-stream", att.filename);
      }
    } catch { /* preview is best-effort */ }
    if (seq === previewSeqRef.current) setPreviewLoading(false);
  };

  return {
    slots,
    rejected,
    fileInputRef,
    previewData,
    previewLoading,
    handleFileSelect,
    removeAttachment,
    dismissRejected,
    closePreview,
    previewAttachment,
    buildWrite,
    reconcile,
  };
}
