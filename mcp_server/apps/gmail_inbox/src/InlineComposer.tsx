import { useEffect, useRef, useState } from "react";
import { CheckCircle, PaperclipHorizontal, Trash, Warning } from "@phosphor-icons/react";
import type {
  ComposerDraft,
  ComposerSaveStatus,
  McpAppLike,
  Thread,
} from "./types";
import {
  confirmSend,
  draftFieldsEqual,
  errMsg,
  extractDraft,
  extractStructuredContent,
  formatFileSize,
  isPreviewable,
  toolErrorText,
} from "./helpers";
import type { AttachmentSlot } from "./attachmentSlots";
import { useComposerAttachments } from "./useComposerAttachments";
import { reportComposerAction } from "./modelContext";
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
  const [saveStatus, setSaveStatus] = useState<ComposerSaveStatus>({ kind: "idle" });
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [discardHover, setDiscardHover] = useState(false);
  const [localThread, setLocalThread] = useState<Thread | null>(thread);
  const [loadingThread, setLoadingThread] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<ComposerDraft | null>(null);
  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The fields the next write should carry: set synchronously on every edit, so
  // a queued write sends what the user typed even before the prop round-trips.
  const fieldsRef = useRef(draft);
  // Every draft write (autosave and send) runs on this one chain, so no two
  // overlap and each builds on the ids the previous reply confirmed. Without it
  // two saves carrying different file sets could land out of order and drop a
  // file, and a send could cite ids an in-flight save had already replaced.
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = <T,>(task: () => Promise<T>): Promise<T> => {
    const run = chainRef.current.then(task, task);
    chainRef.current = run.catch(() => {});
    return run;
  };
  const {
    slots,
    fileInputRef,
    rejected,
    previewData,
    previewLoading,
    handleFileSelect,
    removeAttachment,
    dismissRejected,
    closePreview,
    previewAttachment,
    buildWrite,
    reconcile,
  } = useComposerAttachments(draft, mcpApp, () => scheduleAutoSave());

  // Set once a send/discard is committed. Clearing the debounce timer is not
  // enough: an autosave whose request is already on the wire cannot be recalled,
  // and its late reply would overwrite the terminal "sent" state and drop the
  // user back into an editable composer for a draft Gmail has already sent.
  const closingRef = useRef(false);
  // Bumped whenever a terminal action commits. `closingRef` alone is not enough:
  // a failed send releases it so the composer stays editable, and a pre-send
  // autosave resolving just after that would find the latch open and write
  // "saved" over the send error. A save only reports if its generation is still
  // current, so anything started before a terminal action stays silent forever.
  const writeGenRef = useRef(0);
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; fieldsRef.current = draft; }, [draft]);

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

  const fieldArgs = (d: ComposerDraft): Record<string, unknown> => ({
    draft_id: d.draft_id,
    to: d.to ?? "",
    cc: d.cc ?? "",
    bcc: d.bcc ?? "",
    subject: d.subject ?? "",
    body: d.body ?? "",
  });

  // Built when the write runs, not when it was scheduled: by then an earlier
  // reply may have confirmed new ids or turned an upload into a reference.
  const persistDraft = () => enqueue(async () => {
    if (closingRef.current) return;
    const gen = writeGenRef.current;
    const stale = () => closingRef.current || gen !== writeGenRef.current;
    setSaveStatus({ kind: "saving" });
    const snapshot = fieldsRef.current;
    const write = buildWrite();
    try {
      const args = fieldArgs(snapshot);
      // `undefined` means "omit -> preserve all"; an array (including the empty
      // clear-all list) must be sent, so test against undefined, not truthiness.
      if (write.arg !== undefined) args.attachments = write.arg;
      const raw = await mcpApp.callServerTool({ name: "gmail_composer.save_draft", arguments: args });
      const result = (raw ?? {}) as { isError?: boolean };
      if (result.isError) throw new Error(toolErrorText(raw) ?? "the save failed");
      // Fold the confirmed ids in even when the reply is stale for the status
      // line: they are the server's truth, and the queued send builds on them.
      reconcile(write.sent, extractStructuredContent<ComposerDraft>(raw)?.attachments);
      if (stale()) return;
      setSaveStatus({ kind: "saved", at: new Date() });
      const latest = fieldsRef.current;
      if (latest && draftFieldsEqual(latest, snapshot)) localDirtyRef.current = false;
    } catch (err) {
      if (stale()) return;
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  });

  const scheduleAutoSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => void persistDraft(), 800);
  };

  const updateField = (key: keyof ComposerDraft, value: string) => {
    const next = { ...fieldsRef.current, [key]: value };
    fieldsRef.current = next;
    onDraftChange(next);
    localDirtyRef.current = true;
    scheduleAutoSave();
  };

  // A failed send may still have rewritten the draft (send updates, then
  // sends), which replaces every attachment id. Re-read it so a retry cites
  // live ids; only needed when the send carried an explicit file set.
  const resyncAttachments = async (sent: AttachmentSlot[]) => {
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.refresh",
        arguments: { draft_id: draft.draft_id },
      });
      reconcile(sent, extractStructuredContent<ComposerDraft>(raw)?.attachments);
    } catch { /* the next save reports any real failure */ }
  };

  const onSend = async () => {
    // Single-flight: a repeat click while a send is in flight is a no-op.
    if (closingRef.current) return;
    closingRef.current = true;
    writeGenRef.current++;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus({ kind: "sending" });
    const fields = fieldsRef.current;
    try {
      // Queued behind any in-flight save, so it carries that save's ids.
      const sent = await enqueue(async () => {
        const write = buildWrite();
        const args = fieldArgs(fields);
        // Same omit-vs-explicit rule as save_draft.
        if (write.arg !== undefined) args.attachments = write.arg;
        try {
          return await confirmSend(mcpApp, { ...args, draft_id: draft.draft_id });
        } catch (err) {
          if (write.arg !== undefined) await resyncAttachments(write.sent);
          throw err;
        }
      });
      setSaveStatus({ kind: "sent", message_id: sent.message_id });
      void reportComposerAction(mcpApp, "sent", fields, sent);
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
    writeGenRef.current++;
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDiscard();
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draft.draft_id },
      });
      // Like send, a tool-level failure still resolves: only a confirmed
      // discard may tell the model the draft is gone.
      if (extractStructuredContent<{ discarded?: boolean }>(raw)?.discarded) {
        void reportComposerAction(mcpApp, "discarded", draft);
      }
    } catch { /* discard is best-effort */ }
  };

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    fieldsRef.current = pendingAgent;
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
                <button onClick={(e) => { e.stopPropagation(); removeAttachment(att.key); }} style={attachmentRemoveBtn} title="Remove">×</button>
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
