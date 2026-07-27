import type { CuratedThread } from "./types";
import { relativeTime } from "./helpers";
import { MarkDoneButton, SenderAvatar } from "./MessageComponents";
import {
  chipStyle,
  draftChipStyle,
  draftDiscardBtnStyle,
  labelChipBaseStyle,
  labelChipsRowStyle,
  reasonChipStyle,
  rowFootStyle,
  rowMidStyle,
  rowSnippetStyle,
  rowStyle,
  rowTopStyle,
} from "./styles";

// One row in the curated inbox list.
export function ThreadRow({
  thread: t,
  isSelected,
  showUnread,
  showScores,
  onOpen,
  onMarkDone,
  onDiscardDraft,
}: {
  thread: CuratedThread;
  isSelected: boolean;
  showUnread: boolean;
  showScores: boolean;
  onOpen: () => void;
  onMarkDone: (e: React.MouseEvent) => void;
  onDiscardDraft: (e: React.MouseEvent) => void;
}) {
  return (
    <li
      onClick={onOpen}
      style={{
        ...rowStyle,
        background: isSelected ? "#e8f0fe" : "transparent",
      }}
      data-testid={`row-${t.thread_id}`}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <SenderAvatar from={t.from} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={rowTopStyle}>
            <span
              style={{
                fontWeight: showUnread ? 700 : 500,
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {t.subject || "(no subject)"}
            </span>
            <MarkDoneButton onClick={onMarkDone} size="row" />
            {showScores && (
              <span style={chipStyle} title={t.reasons.join(", ")}>
                {t.importance_score.toFixed(2)}
              </span>
            )}
          </div>
          {((t.labels && t.labels.length > 0) || t.reasons.length > 0 || t.has_draft) && (
            <div style={labelChipsRowStyle}>
              {t.has_draft && (
                <span style={draftChipStyle}>
                  Draft
                  {t.draft_id && (
                    <button
                      onClick={onDiscardDraft}
                      style={draftDiscardBtnStyle}
                      title="Discard draft"
                    >
                      ×
                    </button>
                  )}
                </span>
              )}
              {t.labels?.map((l) => (
                <span
                  key={l.name}
                  style={{
                    ...labelChipBaseStyle,
                    background: l.bg_color,
                    color: l.text_color,
                  }}
                >
                  {l.name}
                </span>
              ))}
              {t.reasons.map((r) => (
                <span key={r} style={reasonChipStyle}>{r}</span>
              ))}
            </div>
          )}
          <div style={rowMidStyle}>{t.from || "(unknown)"}</div>
          <div style={rowSnippetStyle}>{t.snippet || ""}</div>
          <div style={rowFootStyle}>{relativeTime(t.last_message_at)}</div>
        </div>
      </div>
    </li>
  );
}
