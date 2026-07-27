import { useEffect, useRef, useState } from "react";
import { CaretLeft } from "@phosphor-icons/react";
import type {
  Coverage,
  CurateResult,
  CuratedThread,
  ComposerDraft,
  GetCurationResult,
  Thread,
  ThreadMessage,
} from "./types";
import {
  curationToThreads,
  errMsg,
  extractDraft,
  extractStructuredContent,
} from "./helpers";
import { useIsNarrow } from "./hooks";
import { ThreadReader } from "./ThreadReader";
import { InlineComposer } from "./InlineComposer";
import { InboxList } from "./InboxList";
import {
  aiReplyStyles,
  appStyle,
  appStyleNarrow,
  errorStyle,
  mutedStyle,
  readerBackBtnStyle,
  readerPaneNarrowStyle,
  readerPaneStyle,
  readerTopBarStyle,
  statusStyle,
} from "./styles";

// Re-exported so consumers (and tests) keep importing types from "./Inbox".
export type {
  Attachment,
  ComposerDraft,
  Coverage,
  CurateResult,
  CuratedThread,
  CurationRecord,
  Draft,
  DraftAttachment,
  GetCurationResult,
  LabelChip,
  McpAppLike,
  Thread,
  ThreadMessage,
} from "./types";

type InboxProps = {
  mcpApp: import("./types").McpAppLike;
  resultBuffer?: import("./helpers").ToolResultBuffer;
};

export function Inbox({ mcpApp, resultBuffer }: InboxProps) {
  const narrow = useIsNarrow();
  const [viewMode, setViewMode] = useState<"inbox" | "reader">("inbox");
  const [threads, setThreads] = useState<CuratedThread[] | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [unreadRemoved, setUnreadRemoved] = useState<Set<string>>(new Set());
  const [markingDone, setMarkingDone] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showScores, setShowScores] = useState(true);
  const [composerDraft, setComposerDraft] = useState<ComposerDraft | null>(null);
  const composerDraftRef = useRef<ComposerDraft | null>(null);
  useEffect(() => { composerDraftRef.current = composerDraft; }, [composerDraft]);
  // On narrow screens the reader/composer is a separate full-screen view, so
  // opening a composer draft must navigate there - otherwise it stays hidden
  // behind the inbox list.
  useEffect(() => {
    if (narrow && composerDraft) setViewMode("reader");
  }, [narrow, composerDraft]);
  // On wide viewports the layout is always two-pane (the selected thread shows
  // in the right pane), so the full-screen "reader" view must not persist when
  // a narrow viewport widens - otherwise the user is stranded in single-pane.
  useEffect(() => {
    if (!narrow && viewMode === "reader") setViewMode("inbox");
  }, [narrow, viewMode]);
  // Monotonic id for openThread; only the most-recent request may mutate state.
  const openSeqRef = useRef(0);

  // The model-facing gmail_get_thread payload is lean (no inline-image or
  // attachment bytes, to keep the LLM context small). When that lean thread
  // arrives via ontoolresult, silently re-fetch the full version through the
  // app-only open_thread tool so inline images render in the reader.
  // Only called from the ontoolresult handler: the functional-update thread_id
  // check below is the whole race guard (a late upgrade for a thread the user
  // navigated away from no-ops). Do not call this from interactive paths
  // without routing it through openSeqRef like openThread does.
  const upgradeThread = async (thread_id: string) => {
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_inbox.open_thread",
        arguments: { thread_id },
      });
      const full = extractStructuredContent<Thread>(raw);
      if (full && Array.isArray(full.messages) && full.thread_id === thread_id) {
        setThread((cur) => (cur && cur.thread_id === thread_id ? full : cur));
      }
    } catch {
      // Keep the lean thread on failure - text still renders fine.
    }
  };

  useEffect(() => {
    let received = false;
    const handler = (raw: unknown) => {
      const draft = extractDraft(raw);
      if (draft) {
        setComposerDraft(draft);
        return;
      }
      const data = extractStructuredContent<
        CurateResult & Thread & Partial<GetCurationResult>
      >(raw);
      if (data && Array.isArray(data.threads)) {
        received = true;
        setThreads(data.threads);
        setCoverage(null);
        setViewMode("inbox");
      } else if (data && Array.isArray(data.records)) {
        // Banked ledger verdicts (inbox_get_curation): render from persistent
        // curation + surface coverage instead of a fresh recompute.
        received = true;
        setThreads(curationToThreads(data.records));
        setCoverage(data.coverage ?? null);
        setViewMode("inbox");
      } else if (data && typeof data.thread_id === "string" && Array.isArray(data.messages)) {
        received = true;
        setThread(data as unknown as Thread);
        setSelectedId(data.thread_id);
        setViewMode("reader");
        pushThreadContext(data as unknown as Thread);
        void upgradeThread(data.thread_id);
      }
    };
    // Prefer draining the buffer installed at startup: it replays a result the
    // host delivered before this effect ran (so a thread-open renders its reader
    // instead of losing the result). Falls back to a plain handler assignment
    // when no buffer was provided (e.g. unit tests construct Inbox directly).
    if (resultBuffer) {
      resultBuffer.drainInto(handler);
    } else {
      mcpApp.ontoolresult = handler;
    }
    // Last-resort fallback: if nothing was ever received (no buffer AND the live
    // result was still missed), proactively fetch the curated inbox after a delay.
    const timer = setTimeout(() => {
      if (!received) refresh();
    }, 800);
    return () => {
      clearTimeout(timer);
      if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = undefined;
    };
  }, [mcpApp]);

  const pushThreadContext = (data: Thread | null) => {
    if (!data || data.messages.length === 0) {
      mcpApp.callServerTool({
        name: "gmail_inbox.set_focus",
        arguments: { thread_id: null },
      });
      return;
    }
    const lastMsg = data.messages[data.messages.length - 1];
    mcpApp.callServerTool({
      name: "gmail_inbox.set_focus",
      arguments: {
        thread_id: data.thread_id,
        subject: lastMsg?.subject || null,
        from_: lastMsg?.from || null,
        message_count: data.messages.length,
        messages: data.messages.map((m) => ({
          message_id: m.message_id,
          from: m.from,
          to: m.to,
          date: m.date,
          subject: m.subject,
          body_text: m.body_text?.slice(0, 2000) || null,
        })),
      },
    });
  };

  const openThread = async (thread_id: string) => {
    const seq = ++openSeqRef.current;
    setSelectedId(thread_id);
    setThread(null);
    setLoadingThread(true);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_inbox.open_thread",
        arguments: { thread_id },
      });
      if (seq !== openSeqRef.current) return;
      const data = extractStructuredContent<Thread>(raw);
      if (data && Array.isArray(data.messages)) {
        setThread(data);
        pushThreadContext(data);
      }
    } catch (err) {
      if (seq !== openSeqRef.current) return;
      setError(errMsg(err));
    } finally {
      if (seq === openSeqRef.current) setLoadingThread(false);
    }
  };

  const refresh = async () => {
    setStatus(null);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_inbox.refresh",
        arguments: {},
      });
      const data = extractStructuredContent<CurateResult>(raw);
      if (data && Array.isArray(data.threads)) {
        setThreads(data.threads);
        // Deterministic refresh returns no ledger coverage; clear any stale
        // banner left over from a prior inbox_get_curation render.
        setCoverage(null);
      }
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const markRead = async () => {
    if (!selectedId) return;
    setUnreadRemoved((s) => new Set(s).add(selectedId));
    setThreads((cur) =>
      cur ? cur.map((t) =>
        t.thread_id === selectedId
          ? { ...t, labels: (t.labels || []).filter((l) => l.name !== "Unread") }
          : t,
      ) : cur,
    );
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.mark_read",
        arguments: { thread_id: selectedId },
      });
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const archive = async () => {
    if (!selectedId) return;
    const removingId = selectedId;
    setThreads((cur) =>
      cur ? cur.filter((t) => t.thread_id !== removingId) : cur
    );
    setSelectedId(null);
    setThread(null);
    pushThreadContext(null);
    if (narrow) setViewMode("inbox");
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.archive",
        arguments: { thread_id: removingId },
      });
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const sendReply = async (mode: "fast" | "smart", extraContext?: string) => {
    if (!selectedId || !thread) return;
    setStatus(null);
    const lastMsg = thread.messages[thread.messages.length - 1];
    const subject = lastMsg?.subject || "(no subject)";
    const from = lastMsg?.from || "the sender";
    const ctxSuffix = extraContext ? ` Additional context from the user: "${extraContext}"` : "";
    if (mcpApp.sendMessage) {
      const prompt =
        mode === "fast"
          ? `Draft a reply to the email thread "${subject}" from ${from}. Use gmail_get_focused_email for the thread context, then call gmail_reply_to_thread to create the draft. Keep it concise and direct. Do not use bash or shell commands unless explicitly instructed.${ctxSuffix}`
          : `Draft a thorough, well-researched reply to the email thread "${subject}" from ${from}. Before writing, gather all relevant context: use gmail_get_focused_email for the thread, and search across any other available tools, integrations, or knowledge sources (via MCP tools only) for information that would make the reply more informed and useful. Spawn subagents if needed to research in parallel. Do not use bash or shell commands unless explicitly instructed - all context gathering should happen through MCP tools and integrations. Once you have sufficient context, call gmail_reply_to_thread to create the draft.${ctxSuffix}`;
      setStatus(mode === "fast" ? "Drafting reply…" : "Researching context & drafting reply…");
      try {
        await mcpApp.sendMessage({
          role: "user",
          content: [{ type: "text", text: prompt }],
        });
      } catch {
        setStatus(null);
        setError("Could not trigger agent reply");
      }
    } else {
      try {
        await mcpApp.callServerTool({
          name: "gmail_inbox.reply",
          arguments: { thread_id: selectedId },
        });
        setStatus("Reply draft created");
        setTimeout(() => setStatus(null), 3000);
      } catch (err) {
        setError(errMsg(err));
      }
    }
  };
  const fastReply = (context?: string) => sendReply("fast", context);
  const smartReply = (context?: string) => sendReply("smart", context);

  const forwardMessage = async (message: ThreadMessage) => {
    if (!selectedId) return;
    setStatus(null);
    const subject = message.subject || "";
    const body = message.body_text || "";
    const header = `---------- Forwarded message ----------\nFrom: ${message.from || ""}\nDate: ${message.date || ""}\nSubject: ${subject}\nTo: ${message.to || ""}\n\n`;
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.forward",
        arguments: {
          thread_id: selectedId,
          subject,
          body: header + body,
        },
      });
      setStatus("Forward draft created - switch to composer");
      setTimeout(() => setStatus(null), 3000);
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const markDone = async (threadId?: string) => {
    const id = threadId || selectedId;
    if (!id) return;
    setMarkingDone((s) => new Set(s).add(id));
    setThreads((cur) => (cur ? cur.filter((t) => t.thread_id !== id) : cur));
    if (id === selectedId) {
      setSelectedId(null);
      setThread(null);
      pushThreadContext(null);
      if (narrow) setViewMode("inbox");
    }
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.mark_done",
        arguments: { thread_id: id },
      });
    } catch (err) {
      setError(errMsg(err));
      refresh();
    } finally {
      setMarkingDone((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }
  };

  const discardDraft = async (threadId: string, draftId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draftId },
      });
      setThreads((cur) =>
        cur
          ? cur.map((t) =>
              t.thread_id === threadId
                ? { ...t, has_draft: false, draft_id: undefined }
                : t,
            )
          : cur,
      );
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const visibleThreads = threads;

  const readerContent = (
    <>
      {composerDraft ? (
        <InlineComposer
          draft={composerDraft}
          thread={thread}
          mcpApp={mcpApp}
          onDraftChange={setComposerDraft}
          onBack={() => setComposerDraft(null)}
          onDiscard={() => {
            const tid = composerDraft?.thread_id;
            setComposerDraft(null);
            if (tid) {
              setThreads((cur) =>
                cur ? cur.map((t) => t.thread_id === tid ? { ...t, has_draft: false, draft_id: undefined } : t) : cur,
              );
              setThread((cur) => cur && cur.thread_id === tid ? { ...cur, draft: undefined } : cur);
            }
          }}
          onSent={() => {
            const tid = composerDraft?.thread_id || selectedId;
            setComposerDraft(null);
            if (tid) {
              setThreads((cur) =>
                cur ? cur.map((t) => t.thread_id === tid ? { ...t, has_draft: false, draft_id: undefined } : t) : cur,
              );
              setThread((cur) => cur && cur.thread_id === tid ? { ...cur, draft: undefined } : cur);
              openThread(tid);
            }
          }}
        />
      ) : selectedId === null ? (
        <div style={mutedStyle}>Select a thread on the left.</div>
      ) : loadingThread ? (
        <div style={mutedStyle}>Loading thread…</div>
      ) : thread === null ? (
        <div style={mutedStyle}>(empty)</div>
      ) : (
        <ThreadReader
          thread={thread}
          mcpApp={mcpApp}
          onRefresh={refresh}
          onMarkRead={markRead}
          onArchive={archive}
          onMarkDone={() => markDone()}
          onFastReply={fastReply}
          onSmartReply={smartReply}
          onForward={forwardMessage}
          onEditDraft={(d) => setComposerDraft({
            draft_id: d.draft_id,
            to: d.to,
            cc: d.cc,
            subject: d.subject,
            body: d.body,
            thread_id: d.thread_id,
            attachments: d.attachments,
          })}
        />
      )}
      {status && <div style={statusStyle}>{status}</div>}
      {error && (
        <div role="alert" style={errorStyle}>
          {error}
        </div>
      )}
    </>
  );

  if (viewMode === "reader") {
    return (
      <div style={{ ...(narrow ? appStyleNarrow : appStyle), flexDirection: "column" }}>
        <style>{aiReplyStyles}</style>
        {threads && threads.length > 0 && (
          <div style={readerTopBarStyle}>
            <button
              onClick={() => setViewMode("inbox")}
              style={readerBackBtnStyle}
            >
              <CaretLeft size={18} weight="bold" /> Inbox
            </button>
          </div>
        )}
        <main style={narrow ? readerPaneNarrowStyle : { ...readerPaneStyle, flex: 1 }}>
          {readerContent}
        </main>
      </div>
    );
  }

  return (
    <div style={narrow ? appStyleNarrow : appStyle}>
      <style>{aiReplyStyles}</style>
      <InboxList
        narrow={narrow}
        showScores={showScores}
        onToggleScores={() => setShowScores((v) => !v)}
        coverage={coverage}
        threads={visibleThreads}
        selectedId={selectedId}
        unreadRemoved={unreadRemoved}
        onRefresh={refresh}
        onOpenThread={(id) => { setViewMode(narrow ? "reader" : "inbox"); openThread(id); }}
        onMarkDone={(id) => markDone(id)}
        onDiscardDraft={discardDraft}
      />
      {!narrow && (
        <main style={readerPaneStyle}>
          {readerContent}
        </main>
      )}
    </div>
  );
}
