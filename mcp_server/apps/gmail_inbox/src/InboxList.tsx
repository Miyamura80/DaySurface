import { ArrowCounterClockwise } from "@phosphor-icons/react";
import type { Coverage, CuratedThread } from "./types";
import { ThreadRow } from "./ThreadRow";
import {
  iconBtnStyle,
  listHeaderStyle,
  listPaneNarrowStyle,
  listPaneStyle,
  listStyle,
  listStyleNarrow,
  mutedStyle,
} from "./styles";

// The curated-inbox list pane: header, coverage banner, and the thread rows.
export function InboxList({
  narrow,
  showScores,
  onToggleScores,
  coverage,
  threads,
  selectedId,
  unreadRemoved,
  onRefresh,
  onOpenThread,
  onMarkDone,
  onDiscardDraft,
}: {
  narrow: boolean;
  showScores: boolean;
  onToggleScores: () => void;
  coverage: Coverage | null;
  threads: CuratedThread[] | null;
  selectedId: string | null;
  unreadRemoved: Set<string>;
  onRefresh: () => void;
  onOpenThread: (threadId: string) => void;
  onMarkDone: (threadId: string) => void;
  onDiscardDraft: (threadId: string, draftId: string, e: React.MouseEvent) => void;
}) {
  return (
    <aside style={narrow ? listPaneNarrowStyle : listPaneStyle}>
      <header style={listHeaderStyle}>
        <strong style={{ fontSize: 14 }}>Curated inbox</strong>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={onToggleScores}
            style={{ ...iconBtnStyle, fontSize: 11, fontWeight: 600, width: 28, height: 28, color: showScores ? "#1a73e8" : "#5f6368", background: showScores ? "#e8f0fe" : "#fff" }}
            title={showScores ? "Hide scores" : "Show scores"}
          >
            #
          </button>
          <button onClick={onRefresh} style={iconBtnStyle} title="Refresh">
            <ArrowCounterClockwise size={16} />
          </button>
        </div>
      </header>
      {coverage && (
        <div
          data-testid="coverage-banner"
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "#5f6368",
            background: "#f8f9fa",
            borderBottom: "1px solid #ebebeb",
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <span style={{ color: "#188038" }}>{coverage.curated} triaged</span>
          {coverage.stale > 0 && (
            <span style={{ color: "#b06000" }}>{coverage.stale} stale</span>
          )}
          {coverage.uncurated > 0 && (
            <span>{coverage.uncurated} not yet triaged</span>
          )}
        </div>
      )}
      {threads === null ? (
        <div style={mutedStyle}>Loading inbox…</div>
      ) : threads.length === 0 ? (
        <div style={mutedStyle}>No threads.</div>
      ) : (
        <ul style={narrow ? listStyleNarrow : listStyle}>
          {threads.map((t) => {
            const showUnread =
              t.reasons.some((r) => r.toLowerCase().includes("unread")) &&
              !unreadRemoved.has(t.thread_id);
            return (
              <ThreadRow
                key={t.thread_id}
                thread={t}
                isSelected={t.thread_id === selectedId}
                showUnread={showUnread}
                showScores={showScores}
                onOpen={() => onOpenThread(t.thread_id)}
                onMarkDone={(e) => { e.stopPropagation(); onMarkDone(t.thread_id); }}
                onDiscardDraft={(e) => onDiscardDraft(t.thread_id, t.draft_id!, e)}
              />
            );
          })}
        </ul>
      )}
    </aside>
  );
}
