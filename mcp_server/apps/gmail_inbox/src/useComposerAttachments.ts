import { useEffect, useRef, useState } from "react";
import type React from "react";
import type { McpAppLike } from "./types";
import { base64ToBlobUrl, extractStructuredContent, formatFileSize } from "./helpers";
import type { AttachmentSlot } from "./attachmentSlots";
import type { PickedFile } from "./useDraftWriter";
import type { PreviewData } from "./AttachmentPreview";

// Cap a single attachment just under the server's limit: AttachmentInput caps
// base64 at 34M chars (~25.5 MB decoded, models/gmail.py), and Gmail's ceiling
// is 25 MB for the WHOLE message. 25 MB (decimal) keeps a drop safely inside
// the base64 validator, so the client guard actually prevents a wasted read and
// round-trip instead of surfacing a raw validation error later.
export const MAX_ATTACHMENT_BYTES = 25_000_000;
// Spelled out rather than run through formatFileSize: that helper divides by
// 1024^2 while labelling the result "MB", which renders this decimal cap as
// "23.8 MB" - a limit no user would recognise as the documented 25 MB.
const MAX_ATTACHMENT_LABEL = "25 MB";

/** A file the user picked that never made it onto the draft, with the reason. */
export type RejectedAttachment = { filename: string; reason: string };

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/**
 * Picking files and previewing them for the inline composer. Uploading belongs
 * to the draft writer: a pick that passes the preflight goes straight to
 * `addFile` with its read still pending, which queues its upload and resolves to an error if it failed.
 */
export function useComposerAttachments(
  mcpApp: McpAppLike,
  slotsRef: React.RefObject<AttachmentSlot[]>,
  addFile: (file: PickedFile) => Promise<string | null>,
) {
  const [rejected, setRejected] = useState<RejectedAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);
  const previewSeqRef = useRef(0);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  const reject = (r: RejectedAttachment) => setRejected((prev) => [...prev, r]);

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
    // Each file is exactly one slot from the moment it is picked (still reading
    // included), so nothing is counted twice or missed.
    const committedBytes =
      (slotsRef.current ?? []).reduce((sum, a) => sum + (a.size ?? MAX_ATTACHMENT_BYTES), 0);
    const incomingBytes = accepted.reduce((s, f) => s + f.size, 0);
    const fits = committedBytes + incomingBytes <= MAX_ATTACHMENT_BYTES;
    if (!fits) {
      for (const f of accepted) {
        rejects.push({ filename: f.name, reason: `Message would exceed ${MAX_ATTACHMENT_LABEL}` });
      }
    }
    if (rejects.length) setRejected((prev) => [...prev, ...rejects]);
    if (!fits || accepted.length === 0) return;

    for (const file of accepted) {
      void addFile({
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        size: file.size,
        data: readBase64(file),
      }).then((err) => { if (err) reject({ filename: file.name, reason: `Upload failed: ${err}` }); });
    }
  };

  const dismissRejected = (index: number) => {
    setRejected((prev) => prev.filter((_, i) => i !== index));
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
    if (att.kind === "pending") {
      // Still being read: nothing to show yet.
      if (att.data_base64 !== undefined) showPreview(att.data_base64, att.mime_type, att.filename);
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
      const b64 = extractStructuredContent<{ data_base64?: string }>(raw)?.data_base64;
      if (b64) showPreview(b64, att.mime_type || "application/octet-stream", att.filename);
    } catch { /* preview is best-effort */ }
    if (seq === previewSeqRef.current) setPreviewLoading(false);
  };

  return {
    rejected,
    fileInputRef,
    previewData,
    previewLoading,
    handleFileSelect,
    dismissRejected,
    closePreview,
    previewAttachment,
  };
}
