import { useState } from "react";
import { HtmlEmailBody } from "./HtmlEmailBody";
import type { ComposerSaveStatus, McpAppLike, Thread, ThreadMessage } from "./types";
import { relativeTime, splitTextAtQuote } from "./helpers";
import { SenderAvatar } from "./MessageComponents";
import {
  composerBodyHtmlStyle,
  composerBodyTextStyle,
  composerCollapsedMsgStyle,
  composerExpandedMsgStyle,
  composerQuoteToggle,
  composerThreadPanelStyle,
} from "./composerStyles";

export function ComposerThreadPanel({ thread, mcpApp }: { thread: Thread; mcpApp: McpAppLike }) {
  return (
    <div style={composerThreadPanelStyle}>
      {thread.messages.map((m, i) => (
        <ComposerThreadMsg key={m.message_id} message={m} mcpApp={mcpApp} defaultExpanded={i === thread.messages.length - 1} />
      ))}
    </div>
  );
}

function ComposerThreadMsg({ message, mcpApp, defaultExpanded }: { message: ThreadMessage; mcpApp: McpAppLike; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div style={composerCollapsedMsgStyle} onClick={() => setExpanded(true)}>
        <SenderAvatar from={message.from} />
        <strong style={{ fontSize: 13 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#5f6368", fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 80) || message.subject || ""}
        </span>
        <span style={{ color: "#5f6368", fontSize: 11, flexShrink: 0 }}>{relativeTime(message.date)}</span>
      </div>
    );
  }

  return (
    <div style={composerExpandedMsgStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, cursor: "pointer" }} onClick={() => setExpanded(false)}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SenderAvatar from={message.from} />
          <div>
            <strong style={{ fontSize: 14 }}>{message.from || "(unknown)"}</strong>
            {message.to && <div style={{ fontSize: 12, color: "#5f6368" }}>to {message.to}</div>}
          </div>
        </div>
        <span style={{ fontSize: 12, color: "#5f6368" }}>{relativeTime(message.date)}</span>
      </div>
      <ComposerMsgBody message={message} mcpApp={mcpApp} />
    </div>
  );
}

function ComposerMsgBody({ message, mcpApp }: { message: ThreadMessage; mcpApp: McpAppLike }) {
  const [showQuoted, setShowQuoted] = useState(false);

  if (message.body_html) {
    return (
      <HtmlEmailBody
        html={message.body_html}
        mcpApp={mcpApp}
        htmlStyle={composerBodyHtmlStyle}
        quoteToggleStyle={composerQuoteToggle}
      />
    );
  }
  if (message.body_text) {
    const { main, quoted } = splitTextAtQuote(message.body_text);
    return (
      <div>
        <pre style={composerBodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={composerQuoteToggle}>&bull;&bull;&bull;</button>
            {showQuoted && <pre style={{ ...composerBodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}>{quoted}</pre>}
          </>
        )}
      </div>
    );
  }
  return <div style={{ color: "#5f6368", fontSize: 13 }}>(no body)</div>;
}

export function renderComposerStatus(s: ComposerSaveStatus): string {
  switch (s.kind) {
    case "idle": return "";
    case "saving": return "Saving…";
    case "saved": return `Saved at ${s.at.getHours().toString().padStart(2, "0")}:${s.at.getMinutes().toString().padStart(2, "0")}`;
    case "error": return `Error: ${s.message}`;
    case "sending": return "Sending…";
    case "sent": return "Sent!";
  }
}
