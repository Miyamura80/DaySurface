import { useEffect, useState } from "react";
import { CheckCircle, PaperclipHorizontal, Trash, Warning } from "@phosphor-icons/react";
import type { ComposerDraft, McpAppLike, Thread } from "./types";
import {
  draftFieldsEqual,
  extractDraft,
  extractStructuredContent,
  formatFileSize,
  isPreviewable,
} from "./helpers";
import { useComposerAttachments } from "./useComposerAttachments";
import { useDraftWriter } from "./useDraftWriter";
import { ComposerThreadPanel, renderComposerStatus } from "./ComposerThread";
import { PreviewModal } from "./AttachmentPreview";
import { attachmentChipStyle, attachmentRejectedChipStyle } from "./messageStyles";
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
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [discardHover, setDiscardHover] = useState(false);
  const [localThread, setLocalThread] = useState<Thread | null>(thread);
  const [loadingThread, setLoadingThread] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<ComposerDraft | null>(null);
  const writer = useDraftWriter({ draft, mcpApp, onDraftChange, onDiscard, onSent });
  const { saveStatus, slots, updateField } = writer;
  const {
    fileInputRef,
    rejected,
    previewData,
    previewLoading,
    handleFileSelect,
    dismissRejected,
    closePreview,
    previewAttachment,
  } = useComposerAttachments(mcpApp, writer.slotsRef, writer.addFile);
  // Frozen while a send is in flight: the send carries what was on screen at
  // the click, so nothing typed, picked or removed after it may look included.
  const sending = saveStatus.kind === "sending";

  // Listen for agent-initiated draft updates via ontoolresult
  useEffect(() => {
    const prevHandler = mcpApp.ontoolresult;
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) {
        if (prevHandler) prevHandler(raw);
        return;
      }
      const current = writer.fieldsRef.current;
      if (writer.localDirtyRef.current && current && !draftFieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      writer.applyDraft(incoming);
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

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    writer.applyDraft(pendingAgent);
    setPendingAgent(null);
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
            readOnly={sending}
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
              <input type="text" value={draft.cc ?? ""} onChange={(e) => updateField("cc", e.target.value)} readOnly={sending} style={composerInputStyle} aria-label="Cc" />
            </div>
            <div style={composerFieldRow}>
              <span style={composerFieldLabel}>Bcc</span>
              <input type="text" value={draft.bcc ?? ""} onChange={(e) => updateField("bcc", e.target.value)} readOnly={sending} style={composerInputStyle} aria-label="Bcc" />
            </div>
          </>
        )}

        <div style={composerFieldDivider} />

        <textarea
          value={draft.body ?? ""}
          onChange={(e) => updateField("body", e.target.value)}
          readOnly={sending}
          rows={12}
          style={composerTextareaStyle}
          aria-label="Body"
          placeholder="Compose your reply…"
        />

        {/* Attachments: confirmed files and picks still uploading, in order. */}
        {slots.length > 0 && (
          <div style={{ padding: "8px 16px", display: "flex", flexWrap: "wrap", gap: 6 }}>
            {slots.map((att) => (
              <div
                key={att.key}
                style={{ ...attachmentChipStyle, cursor: isPreviewable(att.mime_type) ? "pointer" : "default" }}
                onClick={() => isPreviewable(att.mime_type) && previewAttachment(att)}
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
                {/* A file Gmail stores without an id is kept by every write but can't be removed. */}
                {!sending && (att.kind === "pending" || att.attachment_id) && (
                  <button onClick={(e) => { e.stopPropagation(); writer.removeFile(att.key); }} style={attachmentRemoveBtn} title="Remove">×</button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Files refused before they left the browser (too large / empty). */}
        {rejected.length > 0 && (
          <div style={{ padding: "0 16px 8px", display: "flex", flexWrap: "wrap", gap: 6 }}>
            {rejected.map((r, i) => (
              <div key={`rejected-${i}`} style={attachmentRejectedChipStyle} title={`${r.filename}: ${r.reason}`}>
                <Warning size={12} weight="fill" style={{ marginRight: 4, flexShrink: 0 }} />
                <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.filename}
                </span>
                <span style={{ fontSize: 11, marginLeft: 4 }}>{r.reason}</span>
                <button onClick={() => dismissRejected(i)} style={attachmentRemoveBtn} title="Dismiss">×</button>
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
            <button onClick={writer.send} style={composerSendBtnStyle}>
              {sending ? "Sending…" : "Send"}
            </button>
            <button style={composerToolbarIconBtn} title="Attach files" disabled={sending} onClick={() => fileInputRef.current?.click()}>
              <PaperclipHorizontal size={18} />
            </button>
          </div>
          <div style={composerToolbarRight}>
            <span style={composerSaveStatusStyle(saveStatus)}>
              {renderComposerStatus(saveStatus)}
            </span>
            <button
              onClick={writer.discard}
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
