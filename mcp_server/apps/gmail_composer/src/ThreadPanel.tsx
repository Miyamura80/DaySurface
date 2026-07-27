import { useState } from "react";
import { sanitizeHtml } from "./sanitize";
import type { Thread, ThreadMessage } from "./types";
import { relativeTime, splitHtmlAtQuote, splitTextAtQuote } from "./quote";
import {
  quoteToggleBtnStyle,
  threadBodyHtmlStyle,
  threadBodyTextStyle,
  threadMsgCollapsedStyle,
  threadMsgExpandedStyle,
  threadPanelStyle,
  threadToggleBtn,
} from "./styles";

export function ThreadPanel({
  thread,
  collapsed,
  messagesStyle,
  onToggle,
}: {
  thread: Thread;
  collapsed: boolean;
  messagesStyle: React.CSSProperties;
  onToggle: () => void;
}) {
  return (
    <div style={threadPanelStyle}>
      <button onClick={onToggle} style={threadToggleBtn}>
        {collapsed ? "▶" : "▼"} Conversation ({thread.messages.length} message
        {thread.messages.length === 1 ? "" : "s"})
      </button>
      {!collapsed && (
        <div style={messagesStyle}>
          {thread.messages.map((m, i) => (
            <ThreadMessageView
              key={m.message_id}
              message={m}
              defaultExpanded={i === thread.messages.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ThreadMessageView({
  message,
  defaultExpanded,
}: {
  message: ThreadMessage;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div
        style={threadMsgCollapsedStyle}
        onClick={() => setExpanded(true)}
      >
        <strong style={{ fontSize: 12 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#888", fontSize: 11, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 100) || message.subject || ""}
        </span>
        <span style={{ color: "#999", fontSize: 10, flexShrink: 0 }}>
          {relativeTime(message.date)}
        </span>
      </div>
    );
  }

  return (
    <div style={threadMsgExpandedStyle}>
      <div
        style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, cursor: "pointer" }}
        onClick={() => setExpanded(false)}
      >
        <div>
          <strong style={{ fontSize: 12 }}>{message.from || "(unknown)"}</strong>
          {message.to && <span style={{ fontSize: 11, color: "#666", marginLeft: 8 }}>to {message.to}</span>}
        </div>
        <span style={{ fontSize: 10, color: "#999" }}>{relativeTime(message.date)}</span>
      </div>
      <ThreadMessageBody message={message} />
    </div>
  );
}

function ThreadMessageBody({ message }: { message: ThreadMessage }) {
  const [showQuoted, setShowQuoted] = useState(false);

  if (message.body_html) {
    const { main, quoted } = splitHtmlAtQuote(message.body_html);
    return (
      <div>
        <div style={threadBodyHtmlStyle} dangerouslySetInnerHTML={{ __html: sanitizeHtml(main) }} />
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={quoteToggleBtnStyle}>
              &bull;&bull;&bull;
            </button>
            {showQuoted && (
              <div
                style={{ ...threadBodyHtmlStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(quoted) }}
              />
            )}
          </>
        )}
      </div>
    );
  }
  if (message.body_text) {
    const { main, quoted } = splitTextAtQuote(message.body_text);
    return (
      <div>
        <pre style={threadBodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={quoteToggleBtnStyle}>
              &bull;&bull;&bull;
            </button>
            {showQuoted && (
              <pre style={{ ...threadBodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}>{quoted}</pre>
            )}
          </>
        )}
      </div>
    );
  }
  return <div style={{ color: "#888", fontSize: 12 }}>(no body)</div>;
}
