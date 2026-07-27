import { useEffect, useRef, useState } from "react";
import type {
  Draft,
  DraftAttachment,
  McpAppLike,
  SaveStatus,
  SentState,
  Thread,
} from "./types";
import { draftFields, extractDraft, extractStructuredContent, extractThread, fieldsEqual } from "./draft";
import { discardContextText, sentContextText } from "./modelContext";
import { useAutoGrow, useIsMobile } from "./hooks";
import { ThreadPanel } from "./ThreadPanel";
import {
  AttachmentsSection,
  useAttachments,
  type SaveAttachment,
} from "./attachments";
import {
  agentBannerStyle,
  buttonRowStyle,
  confirmRowStyle,
  containerStyle,
  destructiveButtonStyle,
  headerStyle,
  inputStyle,
  labelStyle,
  linkButtonStyle,
  mobileTextareaStyle,
  mobileThreadMessagesContainer,
  mutedStyle,
  primaryButtonStyle,
  readOnlyStyle,
  rowStyle,
  secondaryButtonStyle,
  smallPrimaryStyle,
  smallSecondaryStyle,
  statusStyle,
  successStyle,
  textareaStyle,
  threadMessagesContainer,
} from "./styles";

// Re-exported so consumers (and tests) keep importing types from "./Composer".
export type { Draft, McpAppLike, Thread, ThreadMessage } from "./types";

type ComposerProps = {
  mcpApp: McpAppLike;
};

export function Composer({ mcpApp }: ComposerProps) {
  const isMobile = useIsMobile();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });
  const [sent, setSent] = useState<SentState | null>(null);
  const [discarded, setDiscarded] = useState(false);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<Draft | null>(null);
  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set once a send/discard is committed (the textarea stays mounted until the
  // RPC resolves): blocks autosave + attachment ops against the terminal draft.
  const closingRef = useRef(false);
  const draftRef = useRef<Draft | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  // The last server-confirmed attachment set - the authoritative keep-list a
  // whole-set-replace save must echo back. Advanced only by server responses
  // (never by optimistic UI), so concurrent mutations build on committed state.
  const attachmentsRef = useRef<DraftAttachment[]>([]);
  // Serializes every draft write (text autosave, attachment add/remove, send)
  // into one chain, so a whole-set-replace save can't interleave with another
  // and drop a file, and a slow save can't land after a newer one.
  const saveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = <T,>(fn: () => Promise<T>): Promise<T> => {
    const run = saveChainRef.current.then(fn, fn);
    saveChainRef.current = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  };

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  // On mobile the body grows to fit its content so there is no inner scroll
  // region competing with the page scroll - the whole iframe scrolls naturally
  // under a finger drag. Desktop keeps its fixed height and own scrollbar.
  useAutoGrow(bodyRef, draft?.body ?? "", isMobile);

  useEffect(() => {
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) return;
      const current = draftRef.current;
      if (!current) {
        setDraft(incoming);
        attachmentsRef.current = incoming.attachments ?? [];
        localDirtyRef.current = false;
        return;
      }
      if (localDirtyRef.current && !fieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      setDraft(incoming);
      attachmentsRef.current = incoming.attachments ?? [];
      localDirtyRef.current = false;
    };
    mcpApp.ontoolresult = handler;
    return () => {
      if (mcpApp.ontoolresult === handler) {
        mcpApp.ontoolresult = undefined;
      }
    };
  }, [mcpApp]);

  // Best-effort: host support for `ui/update-model-context` varies (the MCP
  // Apps extension is young), and the send/discard that triggered the push
  // already succeeded - a failure here must never surface in the UI. Call
  // sites use `void pushModelContext(...)` (never `await`) so a surrounding
  // try/catch structurally cannot repurpose a push failure into an error UI.
  const pushModelContext = async (text: string) => {
    try {
      await mcpApp.updateModelContext({ content: [{ type: "text", text }] });
    } catch {
      // Host rejected or doesn't implement context updates; nothing to do.
    }
  };

  // The single draft-write path. Reads the freshest text off draftRef at call
  // time (never a pre-await snapshot), so a save issued after a slow file read
  // can't revert a live edit. `attachments`: undefined omits the arg (preserve
  // files); an array is a whole-set replace whose echoed result is adopted as
  // truth. Throws on failure so attachment callers can react.
  const doSave = async (attachments?: SaveAttachment[]): Promise<Draft | null> => {
    const snapshot = draftRef.current;
    if (!snapshot) return null;
    setSaveStatus({ kind: "saving" });
    const args: Record<string, unknown> = {
      draft_id: snapshot.draft_id,
      ...draftFields(snapshot),
    };
    if (attachments !== undefined) args.attachments = attachments;
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.save_draft",
        arguments: args,
      });
      const saved = extractDraft(raw);
      if (attachments !== undefined) {
        attachmentsRef.current = saved?.attachments ?? [];
      }
      setSaveStatus({ kind: "saved", at: new Date() });
      // Clear dirty only if nothing newer was typed than the text we just sent.
      const latest = draftRef.current;
      if (latest && fieldsEqual(latest, snapshot)) {
        localDirtyRef.current = false;
      }
      return saved;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
      throw err;
    }
  };

  const scheduleAutoSave = () => {
    // Don't arm a save once the draft is being sent/discarded.
    if (closingRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      // Re-check: a terminal transition may have happened since arming.
      // Text-only save (no attachments arg) preserves existing files.
      if (closingRef.current) return;
      enqueue(() => doSave()).catch(() => {});
    }, 800);
  };

  const updateField = (key: keyof Draft, value: string) => {
    if (!draft) return;
    const next: Draft = { ...draft, [key]: value };
    setDraft(next);
    localDirtyRef.current = true;
    scheduleAutoSave();
  };

  const onSaveNow = () => {
    if (!draftRef.current || closingRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    enqueue(() => doSave()).catch(() => {});
  };

  const attachmentApi = useAttachments({
    draftRef,
    setDraft,
    setSaveStatus,
    attachmentsRef,
    doSave,
    enqueue,
    closingRef,
  });

  const onSend = async () => {
    // Single-flight: a repeat click while a send is in flight is a no-op.
    if (!draftRef.current || closingRef.current) return;
    // Committing to send: block new autosaves against this draft (reset below
    // if the send fails and the composer stays editable).
    closingRef.current = true;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    try {
      // Serialized after any in-flight attachment upload so the files are
      // persisted before we send; reads the freshest text at issue time.
      const raw = await enqueue(() => {
        const cur = draftRef.current;
        if (!cur) throw new Error("no draft to send");
        return mcpApp.callServerTool({
          name: "gmail_composer.send",
          arguments: { draft_id: cur.draft_id, ...draftFields(cur) },
        });
      });
      const inner = extractStructuredContent<{ message_id?: string }>(raw);
      const messageId = inner?.message_id ?? "";
      setSent({ message_id: messageId });
      const cur = draftRef.current;
      if (cur) void pushModelContext(sentContextText(cur, messageId));
    } catch (err) {
      // Send failed: the composer stays editable, so re-enable autosaving.
      closingRef.current = false;
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
    }
  };

  const onDiscardConfirm = async () => {
    const cur = draftRef.current;
    // Single-flight: ignore a repeat confirm while a discard is in flight.
    if (!cur || closingRef.current) return;
    // Committing to discard: block new autosaves (a keystroke during the
    // in-flight delete must not resurrect the draft). Reset on failure.
    closingRef.current = true;
    // Cancel any queued autosave so a debounced save can't race ahead and
    // re-create a row immediately after the discard call returns.
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    try {
      // Serialized so it runs after any in-flight save rather than racing it.
      await enqueue(() =>
        mcpApp.callServerTool({
          name: "gmail_composer.discard",
          arguments: { draft_id: cur.draft_id },
        }),
      );
      setDiscarded(true);
      void pushModelContext(discardContextText(cur));
    } catch (err) {
      // Discard failed: the draft still exists and stays editable.
      closingRef.current = false;
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
    } finally {
      setConfirmingDiscard(false);
    }
  };

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setDraft(pendingAgent);
    attachmentsRef.current = pendingAgent.attachments ?? [];
    setPendingAgent(null);
    localDirtyRef.current = false;
  };

  const keepLocal = () => setPendingAgent(null);

  // Thread context: fetch when draft has a thread_id
  const [thread, setThread] = useState<Thread | null>(null);
  // `null` means "follow the viewport default": collapsed on mobile so the
  // reply box is reachable without scrolling past a long thread, expanded on
  // desktop. Once the user toggles, their explicit choice sticks.
  const [threadCollapsed, setThreadCollapsed] = useState<boolean | null>(null);
  const threadCollapsedEffective = threadCollapsed ?? isMobile;
  const fetchedThreadRef = useRef<string | null>(null);

  useEffect(() => {
    if (!draft?.thread_id) {
      setThread(null);
      fetchedThreadRef.current = null;
      return;
    }
    if (draft.thread_id === fetchedThreadRef.current) return;
    const tid = draft.thread_id;
    setThread(null);
    let cancelled = false;
    mcpApp
      .callServerTool({
        name: "gmail_composer.get_thread",
        arguments: { thread_id: tid },
      })
      .then((raw) => {
        if (cancelled) return;
        fetchedThreadRef.current = tid;
        const data = extractThread(raw);
        if (data) setThread(data);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [draft?.thread_id, mcpApp]);

  if (sent) {
    return (
      <div style={containerStyle}>
        <div style={successStyle}>Sent &#10003; - message_id: {sent.message_id}</div>
      </div>
    );
  }
  if (discarded) {
    return (
      <div style={containerStyle}>
        <div style={mutedStyle}>Discarded.</div>
      </div>
    );
  }
  if (!draft) {
    return <div style={containerStyle}>Waiting for draft…</div>;
  }

  return (
    <div style={containerStyle}>
      {thread && thread.messages.length > 0 && (
        <ThreadPanel
          thread={thread}
          collapsed={threadCollapsedEffective}
          messagesStyle={
            isMobile ? mobileThreadMessagesContainer : threadMessagesContainer
          }
          onToggle={() => setThreadCollapsed(!threadCollapsedEffective)}
        />
      )}

      <header style={headerStyle}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Draft</h2>
        <span style={statusStyle(saveStatus)} data-testid="save-status">
          {renderStatus(saveStatus)}
        </span>
      </header>

      {pendingAgent && (
        <div style={agentBannerStyle} role="status">
          <span>Agent updated this draft.</span>
          <button onClick={applyAgentUpdate} style={smallPrimaryStyle}>
            Apply
          </button>
          <button onClick={keepLocal} style={smallSecondaryStyle}>
            Keep mine
          </button>
        </div>
      )}

      <Row label="From">
        <div style={readOnlyStyle}>{draft.from ?? "(connected account)"}</div>
      </Row>

      <Row label="To">
        <input
          type="text"
          value={draft.to ?? ""}
          onChange={(e) => updateField("to", e.target.value)}
          style={inputStyle}
          aria-label="To"
        />
      </Row>

      {!showCcBcc ? (
        <div style={{ marginBottom: 8 }}>
          <button onClick={() => setShowCcBcc(true)} style={linkButtonStyle}>
            Show Cc/Bcc
          </button>
        </div>
      ) : (
        <>
          <Row label="Cc">
            <input
              type="text"
              value={draft.cc ?? ""}
              onChange={(e) => updateField("cc", e.target.value)}
              style={inputStyle}
              aria-label="Cc"
            />
          </Row>
          <Row label="Bcc">
            <input
              type="text"
              value={draft.bcc ?? ""}
              onChange={(e) => updateField("bcc", e.target.value)}
              style={inputStyle}
              aria-label="Bcc"
            />
          </Row>
        </>
      )}

      <Row label="Subject">
        <input
          type="text"
          value={draft.subject ?? ""}
          onChange={(e) => updateField("subject", e.target.value)}
          style={inputStyle}
          aria-label="Subject"
        />
      </Row>

      <textarea
        ref={bodyRef}
        value={draft.body ?? ""}
        onChange={(e) => updateField("body", e.target.value)}
        rows={14}
        style={isMobile ? mobileTextareaStyle : textareaStyle}
        aria-label="Body"
      />

      <AttachmentsSection
        attachments={draft.attachments ?? []}
        uploads={attachmentApi.uploads}
        dragActive={attachmentApi.dragActive}
        setDragActive={attachmentApi.setDragActive}
        fileInputRef={attachmentApi.fileInputRef}
        onFilesChosen={attachmentApi.onFilesChosen}
        onRemoveAttachment={attachmentApi.onRemoveAttachment}
        dismissUpload={attachmentApi.dismissUpload}
        onDrop={attachmentApi.onDrop}
      />

      <div style={buttonRowStyle}>
        <button onClick={onSend} style={primaryButtonStyle}>
          Send
        </button>
        <button onClick={onSaveNow} style={secondaryButtonStyle}>
          Save draft
        </button>
        {!confirmingDiscard ? (
          <button
            onClick={() => setConfirmingDiscard(true)}
            style={destructiveButtonStyle}
          >
            Discard
          </button>
        ) : (
          <span style={confirmRowStyle}>
            Discard?
            <button onClick={onDiscardConfirm} style={destructiveButtonStyle}>
              Yes, discard
            </button>
            <button
              onClick={() => setConfirmingDiscard(false)}
              style={secondaryButtonStyle}
            >
              Cancel
            </button>
          </span>
        )}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={rowStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

function renderStatus(s: SaveStatus): string {
  switch (s.kind) {
    case "idle":
      return "";
    case "saving":
      return "Saving…";
    case "saved":
      return `Saved at ${s.at.getHours().toString().padStart(2, "0")}:${s.at
        .getMinutes()
        .toString()
        .padStart(2, "0")}`;
    case "error":
      return `Save failed: ${s.message}`;
  }
}
