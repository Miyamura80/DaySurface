import { useEffect, useRef, useState } from "react";
import type React from "react";
import type {
  ComposerDraft,
  DraftAttachment,
  ExistingAttachment,
  FileAttachment,
  McpAppLike,
} from "./types";
import { base64ToBlobUrl, extractStructuredContent } from "./helpers";
import type { PreviewData } from "./AttachmentPreview";

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
    Array.from(files).forEach((file) => {
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
    e.target.value = "";
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
    attachmentsDirtyRef,
    fileInputRef,
    previewData,
    previewLoading,
    handleFileSelect,
    removeAttachment,
    closePreview,
    previewNewAttachment,
    previewExistingAttachment,
  };
}
