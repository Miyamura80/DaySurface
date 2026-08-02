import { useEffect, useRef, useState } from "react";
import type React from "react";
import type {
  ComposerDraft,
  DraftAttachment,
  ExistingAttachment,
  FileAttachment,
  McpAppLike,
} from "./types";
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

function toExisting(attachments: DraftAttachment[]): ExistingAttachment[] {
  return attachments
    .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
    .map((a) => ({
      filename: a.filename,
      mime_type: a.mime_type,
      size: a.size,
      attachment_id: a.attachment_id,
      message_id: a.message_id,
    }));
}

/**
 * Attachment state and the preview modal for the inline composer: the files
 * already on the draft, the ones the user just picked, and the blob-URL
 * lifecycle behind previewing either. Kept apart from the composer's draft
 * persistence, which is the only other thing that reads `attachmentsDirtyRef`.
 */
export function useComposerAttachments(draft: ComposerDraft, mcpApp: McpAppLike) {
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [existingAttachments, setExistingAttachments] = useState<ExistingAttachment[]>(
    () => toExisting(draft.attachments || []),
  );
  const [rejected, setRejected] = useState<RejectedAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // True once the user adds or removes an attachment. Until then, save/send
  // omit the `attachments` argument so the backend preserves every existing
  // file; after a change we send the explicit desired set so a removal sticks.
  const attachmentsDirtyRef = useRef(false);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);
  const previewSeqRef = useRef(0);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  // Sync existing attachments when draft updates (e.g. from refresh or agent)
  useEffect(() => {
    if (!draft.attachments?.length) return;
    setExistingAttachments(toExisting(draft.attachments));
  }, [draft.attachments]);

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
    // unknown existing size: ExistingAttachment.size is optional, and coalescing
    // a missing one to 0 would undercount and let an over-limit batch through,
    // so treat unknown as "cannot fit" rather than guessing low.
    const committedBytes =
      existingAttachments.reduce((s, a) => s + (a.size ?? MAX_ATTACHMENT_BYTES), 0) +
      attachments.reduce((s, a) => s + a.size, 0);
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

    accepted.forEach((file) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        const base64 = result.split(",")[1] || "";
        attachmentsDirtyRef.current = true;
        setAttachments((prev) => [
          ...prev,
          { filename: file.name, mime_type: file.type || "application/octet-stream", data_base64: base64, size: file.size },
        ]);
      };
      reader.readAsDataURL(file);
    });
  };

  const dismissRejected = (index: number) => {
    setRejected((prev) => prev.filter((_, i) => i !== index));
  };

  const removeAttachment = (index: number) => {
    attachmentsDirtyRef.current = true;
    setAttachments((prev) => prev.filter((_, i) => i !== index));
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

  const previewNewAttachment = (att: FileAttachment) => {
    showPreview(att.data_base64, att.mime_type, att.filename);
  };

  const previewExistingAttachment = async (att: ExistingAttachment) => {
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
    attachments,
    existingAttachments,
    rejected,
    attachmentsDirtyRef,
    fileInputRef,
    previewData,
    previewLoading,
    handleFileSelect,
    removeAttachment,
    dismissRejected,
    closePreview,
    previewNewAttachment,
    previewExistingAttachment,
  };
}
