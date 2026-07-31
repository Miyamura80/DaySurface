import { useEffect, useRef, useState } from "react";
import { CheckCircle, PaperclipHorizontal, Trash } from "@phosphor-icons/react";
import type {
  ComposerDraft,
  ComposerSaveStatus,
  McpAppLike,
  Thread,
} from "./types";
import {
  buildAttachmentsPayload,
  draftFieldsEqual,
  errMsg,
  extractDraft,
  extractStructuredContent,
  formatFileSize,
  isPreviewable,
} from "./helpers";
import { useComposerAttachments } from "./useComposerAttachments";
import { ComposerThreadPanel, renderComposerStatus } from "./ComposerThread";
import { PreviewModal } from "./AttachmentPreview";
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
  const {
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
  } = useComposerAttachments(draft, mcpApp);

  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set once a send/discard is committed. Clearing the debounce timer is not
  // enough: an autosave whose request is already on the wire cannot be recalled,
  // and its late reply would overwrite the terminal "sent" state and drop the
  // user back into an editable composer for a draft Gmail has already sent.
  const closingRef = useRef(false);
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

  const persistDraft = async (d: ComposerDraft) => {
    if (closingRef.current) return;
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
      // Re-check after the await, not just on entry: a send committed while this
      // request was in flight makes the reply stale, whichever way it resolved.
      if (closingRef.current) return;
      setSaveStatus({ kind: "saved", at: new Date() });
      const latest = draftRef.current;
      if (latest && draftFieldsEqual(latest, snapshot)) localDirtyRef.current = false;
    } catch (err) {
      if (closingRef.current) return;
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
    // Single-flight: a repeat click while a send is in flight is a no-op.
    if (closingRef.current) return;
    closingRef.current = true;
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
      // callServerTool resolves on a tool-level failure (isError) too, so only a
      // server-confirmed message_id counts as sent. Since "sent" is terminal, a
      // false positive here would be unrecoverable.
      const inner = extractStructuredContent<{ message_id?: string }>(raw);
      const msgId = inner?.message_id ?? "";
      if (!msgId) throw new Error("the server did not confirm the send");
      setSaveStatus({ kind: "sent", message_id: msgId });
      setTimeout(onSent, 1500);
    } catch (err) {
      // The send did not land, so the composer stays editable: reopen it to
      // autosaves and let the user retry.
      closingRef.current = false;
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const onDiscardNow = async () => {
    if (closingRef.current) return;
    closingRef.current = true;
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
