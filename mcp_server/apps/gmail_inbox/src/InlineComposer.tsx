import { useEffect, useRef, useState } from "react";
import { CheckCircle, PaperclipHorizontal, Trash } from "@phosphor-icons/react";
import type {
  ComposerDraft,
  ComposerSaveStatus,
  DraftAttachment,
  ExistingAttachment,
  FileAttachment,
  McpAppLike,
  Thread,
} from "./types";
import {
  base64ToBlobUrl,
  buildAttachmentsPayload,
  draftFieldsEqual,
  errMsg,
  extractDraft,
  extractStructuredContent,
  formatFileSize,
  isPreviewable,
} from "./helpers";
import { ComposerThreadPanel, renderComposerStatus } from "./ComposerThread";
import { PreviewModal, type PreviewData } from "./AttachmentPreview";
import { attachmentChipStyle } from "./messageStyles";
import {
  attachmentRemoveBtn,
  composerAgentApplyBtn,
  composerAgentBanner,
  composerAgentKeepBtn,
  composerBackBtnStyle,
  composerCardStyle,
  composerCcBccToggle,
  composerFieldDivider,
  composerFieldLabel,
  composerFieldRow,
  composerInputStyle,
  composerSaveStatusStyle,
  composerSendBtnStyle,
  composerSentStyle,
  composerSubjectStyle,
  composerTextareaStyle,
  composerToolbarIconBtn,
  composerToolbarLeft,
  composerToolbarRight,
  composerToolbarStyle,
  composerTrashBtn,
} from "./composerStyles";

export function InlineComposer({
  draft,
  thread,
  mcpApp,
  onDraftChange,
  onBack,
  onDiscard,
  onSent,
}: {
  draft: ComposerDraft;
  thread: Thread | null;
  mcpApp: McpAppLike;
  onDraftChange: (d: ComposerDraft) => void;
  onBack: () => void;
  onDiscard: () => void;
  onSent: () => void;
}) {
  const [saveStatus, setSaveStatus] = useState<ComposerSaveStatus>({ kind: "idle" });
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [discardHover, setDiscardHover] = useState(false);
  const [localThread, setLocalThread] = useState<Thread | null>(thread);
  const [loadingThread, setLoadingThread] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<ComposerDraft | null>(null);
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [existingAttachments, setExistingAttachments] = useState<ExistingAttachment[]>(
    () => (draft.attachments || [])
      .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
      .map((a) => ({ filename: a.filename, mime_type: a.mime_type, size: a.size, attachment_id: a.attachment_id, message_id: a.message_id })),
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  // True once the user adds or removes an attachment. Until then, save/send
  // omit the `attachments` argument so the backend preserves every existing
  // file; after a change we send the explicit desired set so a removal sticks.
  const attachmentsDirtyRef = useRef(false);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);

  // Listen for agent-initiated draft updates via ontoolresult
  useEffect(() => {
    const prevHandler = mcpApp.ontoolresult;
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) {
        if (prevHandler) prevHandler(raw);
        return;
      }
      const current = draftRef.current;
      if (localDirtyRef.current && current && !draftFieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      onDraftChange(incoming);
      localDirtyRef.current = false;
    };
    mcpApp.ontoolresult = handler;
    return () => { if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = prevHandler; };
  }, [mcpApp]);

  // Auto-fetch thread context
  useEffect(() => {
    if (localThread || loadingThread) return;
    const threadId = draft.thread_id;
    if (!threadId) return;
    let cancelled = false;
    setLoadingThread(true);
    mcpApp.callServerTool({
      name: "gmail_composer.get_thread",
      arguments: { thread_id: threadId },
    }).then((raw) => {
      if (cancelled) return;
      const t = extractStructuredContent<Thread>(raw);
      if (t && Array.isArray(t.messages)) setLocalThread(t);
    }).catch(() => {}).finally(() => { if (!cancelled) setLoadingThread(false); });
    return () => { cancelled = true; };
  }, [draft.thread_id]);

  // Auto-refresh draft fields if they arrived empty
  useEffect(() => {
    if (!draft.draft_id) return;
    if (draft.to || draft.subject || draft.body) return;
    let cancelled = false;
    mcpApp.callServerTool({
      name: "gmail_composer.refresh",
      arguments: { draft_id: draft.draft_id },
    }).then((raw) => {
      if (cancelled) return;
      const d = extractStructuredContent<ComposerDraft>(raw);
      if (d && d.draft_id) onDraftChange({ ...draft, ...d });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [draft.draft_id]);

  // Sync existing attachments when draft updates (e.g. from refresh or agent)
  useEffect(() => {
    if (!draft.attachments?.length) return;
    setExistingAttachments(
      draft.attachments
        .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
        .map((a) => ({ filename: a.filename, mime_type: a.mime_type, size: a.size, attachment_id: a.attachment_id, message_id: a.message_id })),
    );
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

  const previewSeqRef = useRef(0);

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

  const persistDraft = async (d: ComposerDraft) => {
    setSaveStatus({ kind: "saving" });
    const snapshot = d;
    try {
      const args: Record<string, unknown> = {
        draft_id: snapshot.draft_id,
        to: snapshot.to ?? "",
        cc: snapshot.cc ?? "",
        bcc: snapshot.bcc ?? "",
        subject: snapshot.subject ?? "",
        body: snapshot.body ?? "",
      };
      // Preserve existing files (by reference) alongside new uploads; a bare
      // new-uploads list would replace the whole set and drop them.
      const attachmentsArg = buildAttachmentsPayload(
        attachments,
        existingAttachments,
        attachmentsDirtyRef.current,
      );
      // `undefined` means "omit -> preserve all"; an array (including the empty
      // clear-all list) must be sent, so test against undefined, not truthiness.
      if (attachmentsArg !== undefined) args.attachments = attachmentsArg;
      await mcpApp.callServerTool({ name: "gmail_composer.save_draft", arguments: args });
      setSaveStatus({ kind: "saved", at: new Date() });
      const latest = draftRef.current;
      if (latest && draftFieldsEqual(latest, snapshot)) localDirtyRef.current = false;
    } catch (err) {
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const scheduleAutoSave = (next: ComposerDraft) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => void persistDraft(next), 800);
  };

  const updateField = (key: keyof ComposerDraft, value: string) => {
    const next = { ...draft, [key]: value };
    onDraftChange(next);
    localDirtyRef.current = true;
    scheduleAutoSave(next);
  };

  const onSend = async () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus({ kind: "sending" });
    try {
      const args: Record<string, unknown> = {
        draft_id: draft.draft_id,
        to: draft.to ?? "",
        cc: draft.cc ?? "",
        bcc: draft.bcc ?? "",
        subject: draft.subject ?? "",
        body: draft.body ?? "",
      };
      // Same preservation as save_draft: keep existing files when new ones are added.
      const attachmentsArg = buildAttachmentsPayload(
        attachments,
        existingAttachments,
        attachmentsDirtyRef.current,
      );
      // `undefined` means "omit -> preserve all"; an array (including the empty
      // clear-all list) must be sent, so test against undefined, not truthiness.
      if (attachmentsArg !== undefined) args.attachments = attachmentsArg;
      const raw = await mcpApp.callServerTool({ name: "gmail_composer.send", arguments: args });
      const inner = extractStructuredContent<{ message_id?: string }>(raw);
      const msgId = inner?.message_id ?? "";
      setSaveStatus({ kind: "sent", message_id: msgId });
      setTimeout(onSent, 1500);
    } catch (err) {
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const onDiscardNow = async () => {
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDiscard();
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draft.draft_id },
      });
    } catch { /* discard is best-effort */ }
  };

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDraftChange(pendingAgent);
    setPendingAgent(null);
    localDirtyRef.current = false;
  };

  if (saveStatus.kind === "sent") {
    return (
      <div style={{ padding: 16 }}>
        <div style={composerSentStyle}>
          <CheckCircle size={20} weight="fill" style={{ marginRight: 6, verticalAlign: "middle" }} />
          Message sent
        </div>
      </div>
    );
  }

  const effectiveThread = localThread || thread;
  const allMsgs = effectiveThread?.messages ?? [];
  const sentMessages = allMsgs;
  const first = sentMessages[0] ?? allMsgs[0];
  const subject = first?.subject || draft.subject || "(no subject)";

  return (
    <div style={{ fontFamily: "'Google Sans', Roboto, Arial, sans-serif" }}>
      <button onClick={onBack} style={composerBackBtnStyle}>
        ← Back to inbox
      </button>

      <h2 style={composerSubjectStyle}>{subject}</h2>

      {loadingThread && (
        <div style={{ color: "#5f6368", fontSize: 13, padding: "8px 0" }}>Loading conversation…</div>
      )}
      {sentMessages.length > 0 && (
        <ComposerThreadPanel thread={{ ...effectiveThread!, messages: sentMessages }} mcpApp={mcpApp} />
      )}

      {pendingAgent && (
        <div style={composerAgentBanner}>
          <span>Agent updated this draft.</span>
          <button onClick={applyAgentUpdate} style={composerAgentApplyBtn}>Apply</button>
          <button onClick={() => setPendingAgent(null)} style={composerAgentKeepBtn}>Keep mine</button>
        </div>
      )}

      {/* --- Compose card (Gmail Material 3 elevation) --- */}
      <div style={composerCardStyle}>
        <div style={composerFieldRow}>
          <span style={composerFieldLabel}>To</span>
          <input
            type="text"
            value={draft.to ?? ""}
            onChange={(e) => updateField("to", e.target.value)}
            style={composerInputStyle}
            aria-label="To"
          />
          {!showCcBcc && (
            <button onClick={() => setShowCcBcc(true)} style={composerCcBccToggle}>Cc/Bcc</button>
          )}
        </div>

        {showCcBcc && (
          <>
            <div style={composerFieldRow}>
              <span style={composerFieldLabel}>Cc</span>
              <input type="text" value={draft.cc ?? ""} onChange={(e) => updateField("cc", e.target.value)} style={composerInputStyle} aria-label="Cc" />
            </div>
            <div style={composerFieldRow}>
              <span style={composerFieldLabel}>Bcc</span>
              <input type="text" value={draft.bcc ?? ""} onChange={(e) => updateField("bcc", e.target.value)} style={composerInputStyle} aria-label="Bcc" />
            </div>
          </>
        )}

        <div style={composerFieldDivider} />

        <textarea
          value={draft.body ?? ""}
          onChange={(e) => updateField("body", e.target.value)}
          rows={12}
          style={composerTextareaStyle}
          aria-label="Body"
          placeholder="Compose your reply…"
        />

        {/* Attachments list (existing + newly added) */}
        {(existingAttachments.length > 0 || attachments.length > 0) && (
          <div style={{ padding: "8px 16px", display: "flex", flexWrap: "wrap", gap: 6 }}>
            {existingAttachments.map((att, i) => (
              <div
                key={`existing-${i}`}
                style={{ ...attachmentChipStyle, cursor: isPreviewable(att.mime_type) ? "pointer" : "default" }}
                onClick={() => isPreviewable(att.mime_type) && previewExistingAttachment(att)}
                title={isPreviewable(att.mime_type) ? "Click to preview" : att.filename}
              >
                <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
                <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {att.filename}
                </span>
                {att.size != null && (
                  <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                    {formatFileSize(att.size)}
                  </span>
                )}
              </div>
            ))}
            {attachments.map((att, i) => (
              <div
                key={`new-${i}`}
                style={{ ...attachmentChipStyle, cursor: isPreviewable(att.mime_type) ? "pointer" : "default" }}
                onClick={() => isPreviewable(att.mime_type) && previewNewAttachment(att)}
                title={isPreviewable(att.mime_type) ? "Click to preview" : att.filename}
              >
                <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
                <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {att.filename}
                </span>
                <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                  {formatFileSize(att.size)}
                </span>
                <button onClick={(e) => { e.stopPropagation(); removeAttachment(i); }} style={attachmentRemoveBtn} title="Remove">×</button>
              </div>
            ))}
          </div>
        )}

        {previewLoading && (
          <div style={{ padding: "8px 16px", fontSize: 13, color: "#5f6368" }}>Loading preview…</div>
        )}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={handleFileSelect}
        />

        {/* Toolbar */}
        <div style={composerToolbarStyle}>
          <div style={composerToolbarLeft}>
            <button onClick={onSend} style={composerSendBtnStyle}>
              {saveStatus.kind === "sending" ? "Sending…" : "Send"}
            </button>
            <button style={composerToolbarIconBtn} title="Attach files" onClick={() => fileInputRef.current?.click()}>
              <PaperclipHorizontal size={18} />
            </button>
          </div>
          <div style={composerToolbarRight}>
            <span style={composerSaveStatusStyle(saveStatus)}>
              {renderComposerStatus(saveStatus)}
            </span>
            <button
              onClick={onDiscardNow}
              style={{
                ...composerTrashBtn,
                color: discardHover ? "#d93025" : "#5f6368",
                background: discardHover ? "#fce8e6" : "transparent",
              }}
              title="Discard draft"
              onMouseEnter={() => setDiscardHover(true)}
              onMouseLeave={() => setDiscardHover(false)}
            >
              <Trash size={18} />
            </button>
          </div>
        </div>
      </div>

      {/* Attachment preview modal */}
      {previewData && <PreviewModal preview={previewData} onClose={closePreview} />}
    </div>
  );
}
