import { useState } from "react";
import {
  ArrowBendUpLeft,
  ArrowBendUpRight,
  CheckCircle,
  PaperclipHorizontal,
} from "@phosphor-icons/react";
import { HtmlEmailBody } from "./HtmlEmailBody";
import type { Draft, McpAppLike, ThreadMessage } from "./types";
import {
  formatDate,
  formatFileSize,
  isPreviewable,
  relativeTime,
  splitTextAtQuote,
} from "./helpers";
import { draftChipStyle, iconBtnStyle, mutedStyle } from "./styles";
import {
  attachmentChipStyle,
  attachmentsRowStyle,
  bodyHtmlStyle,
  bodyTextStyle,
  collapsedMessageStyle,
  collapsedReplyBtnStyle,
  draftBodyStyle,
  draftCardStyle,
  imageAttachmentStyle,
  messageActionBtnStyle,
  messageActionsStyle,
  messageHeaderStyle,
  messageStyle,
  quoteToggleStyle,
} from "./messageStyles";

export type PreviewAttachment = {
  filename?: string;
  mime_type?: string;
  attachment_id?: string;
  message_id?: string;
  data?: string;
};

export function MarkDoneButton({ onClick, size = "row" }: { onClick: (e: React.MouseEvent) => void; size?: "row" | "action" }) {
  const [hovered, setHovered] = useState(false);
  const isAction = size === "action";
  const baseStyle: React.CSSProperties = isAction
    ? {
        ...iconBtnStyle,
        background: hovered ? "#e6f4ea" : "#fff",
        borderColor: hovered ? "#34a853" : "#dadce0",
        color: hovered ? "#137333" : "#5f6368",
        transform: hovered ? "scale(1.15)" : "scale(1)",
        transition: "all 0.15s ease",
      }
    : {
        background: "none",
        border: "none",
        color: hovered ? "#137333" : "#aaa",
        padding: 2,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        borderRadius: 4,
        transform: hovered ? "scale(1.3)" : "scale(1)",
        transition: "all 0.15s ease",
        backgroundColor: hovered ? "#e6f4ea" : "transparent",
      };
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={baseStyle}
      title="Mark done"
    >
      <CheckCircle size={isAction ? 16 : 14} weight={hovered ? "fill" : "regular"} />
    </button>
  );
}

export function SenderAvatar({ from }: { from: string | undefined }) {
  const name = from || "";
  const match = name.match(/^([^<]*)/);
  const display = (match?.[1] || name).trim();
  const initials = display
    ? display
        .split(/[\s.]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0].toUpperCase())
        .join("")
    : "?";
  const hue = [...(display || "?")].reduce((h, c) => h + c.charCodeAt(0), 0) % 360;
  return (
    <div
      style={{
        width: 32,
        height: 32,
        borderRadius: "50%",
        background: `hsl(${hue}, 55%, 55%)`,
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        fontWeight: 600,
        flexShrink: 0,
        letterSpacing: 0.5,
      }}
      title={from || "(unknown)"}
    >
      {initials}
    </div>
  );
}

export function CollapsibleMessage({
  message,
  mcpApp,
  onReply,
  onForward,
  onPreview,
  defaultExpanded,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
  onReply: () => void;
  onForward: () => void;
  onPreview: (att: PreviewAttachment) => void;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div
        style={collapsedMessageStyle}
        onClick={() => setExpanded(true)}
        data-testid={`collapsed-${message.message_id}`}
      >
        <SenderAvatar from={message.from} />
        <strong style={{ fontSize: 13 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#888", fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 80) || message.subject || ""}
        </span>
        <span style={{ color: "#999", fontSize: 11, flexShrink: 0 }} title={formatDate(message.date)}>
          {relativeTime(message.date)}
        </span>
        <button
          style={collapsedReplyBtnStyle}
          title="Reply"
          onClick={(e) => { e.stopPropagation(); onReply(); }}
        >
          <ArrowBendUpLeft size={14} />
        </button>
      </div>
    );
  }

  return (
    <MessageView
      message={message}
      mcpApp={mcpApp}
      onReply={onReply}
      onForward={onForward}
      onPreview={onPreview}
      onCollapse={() => setExpanded(false)}
    />
  );
}

export function MessageView({
  message,
  mcpApp,
  onReply,
  onForward,
  onPreview,
  onCollapse,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
  onReply: () => void;
  onForward: () => void;
  onPreview: (att: PreviewAttachment) => void;
  onCollapse?: () => void;
}) {
  // Non-inline image attachments (inline ones are already in the HTML via data URIs)
  const imageAttachments = message.attachments.filter(
    (a) => a.mime_type?.startsWith("image/") && !a.content_id
  );
  const otherAttachments = message.attachments.filter(
    (a) => !a.mime_type?.startsWith("image/")
  );

  return (
    <article style={messageStyle} data-testid={`msg-${message.message_id}`}>
      <header
        style={{ ...messageHeaderStyle, cursor: onCollapse ? "pointer" : undefined }}
        onClick={onCollapse}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SenderAvatar from={message.from} />
          <div>
            <strong>{message.from || "(unknown)"}</strong>
            {message.to && (
              <div style={{ fontSize: 12, color: "#666" }}>to {message.to}</div>
            )}
            {message.cc && (
              <div style={{ fontSize: 12, color: "#666" }}>cc {message.cc}</div>
            )}
          </div>
        </div>
        <div
          style={{ color: "#666", fontSize: 12, flexShrink: 0 }}
          title={formatDate(message.date)}
        >
          {relativeTime(message.date)}
        </div>
      </header>
      <MessageBody message={message} mcpApp={mcpApp} />
      {imageAttachments.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
          {imageAttachments.map((a, i) => (
            <div
              key={a.attachment_id || `${message.message_id}-img-${i}`}
              style={{ ...imageAttachmentStyle, cursor: "pointer" }}
              data-testid="image-attachment"
              onClick={() => onPreview({ ...a, message_id: message.message_id })}
              title="Click to preview"
            >
              {a.data ? (
                <img
                  src={`data:${a.mime_type || "image/png"};base64,${a.data}`}
                  alt={a.filename || "attachment"}
                  style={{ maxWidth: "100%", borderRadius: 4 }}
                />
              ) : (
                <div style={{ padding: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: "#555" }}>
                    {a.filename || "image"}
                  </div>
                  <div style={{ fontSize: 11, color: "#999" }}>
                    {a.mime_type}{typeof a.size === "number" && ` · ${formatFileSize(a.size)}`}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {otherAttachments.length > 0 && (
        <div style={attachmentsRowStyle}>
          {otherAttachments.map((a, i) => (
            <span
              key={a.attachment_id || `${message.message_id}-att-${i}`}
              style={{ ...attachmentChipStyle, cursor: isPreviewable(a.mime_type) ? "pointer" : "default" }}
              data-testid="attachment-chip"
              onClick={() => isPreviewable(a.mime_type) && onPreview({ ...a, message_id: message.message_id })}
              title={isPreviewable(a.mime_type) ? "Click to preview" : a.filename || "(file)"}
            >
              {a.filename || "(file)"}
              {typeof a.size === "number" && (
                <span style={{ color: "#888", marginLeft: 6 }}>
                  {formatFileSize(a.size)}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
      <div style={messageActionsStyle}>
        <button onClick={onReply} style={messageActionBtnStyle} title="Reply">
          <ArrowBendUpLeft size={14} /> Reply
        </button>
        <button onClick={onForward} style={messageActionBtnStyle} title="Forward">
          <ArrowBendUpRight size={14} /> Forward
        </button>
      </div>
    </article>
  );
}

function MessageBody({
  message,
  mcpApp,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
}) {
  const [showQuoted, setShowQuoted] = useState(false);

  const handleLinkClick = (e: React.MouseEvent<HTMLElement>) => {
    const anchor = (e.target as HTMLElement).closest("a");
    if (!anchor) return;
    const href = anchor.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    e.stopPropagation();
    try {
      const scheme = new URL(href).protocol;
      if (!["https:", "http:", "mailto:"].includes(scheme)) return;
    } catch {
      return;
    }
    mcpApp.openLink({ url: href });
  };

  if (message.body_html) {
    return (
      <HtmlEmailBody
        html={message.body_html}
        mcpApp={mcpApp}
        htmlStyle={bodyHtmlStyle}
        quoteToggleStyle={quoteToggleStyle}
        onClick={handleLinkClick}
      />
    );
  }
  if (message.body_text) {
    const { main, quoted } = splitTextAtQuote(message.body_text);
    return (
      <div>
        <pre style={bodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button
              onClick={() => setShowQuoted((v) => !v)}
              style={quoteToggleStyle}
              title={showQuoted ? "Hide quoted text" : "Show quoted text"}
            >
              •••
            </button>
            {showQuoted && (
              <pre style={{ ...bodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 10, marginTop: 4 }}>{quoted}</pre>
            )}
          </>
        )}
      </div>
    );
  }
  return <div style={mutedStyle}>(no body)</div>;
}

export function DraftCard({ draft, onEdit, onPreview }: { draft: Draft; onEdit?: () => void; onPreview?: (att: { filename?: string; mime_type?: string; attachment_id?: string; message_id?: string }) => void }) {
  const atts = draft.attachments?.filter((a) => a.filename) ?? [];
  return (
    <article
      style={{ ...draftCardStyle, cursor: onEdit ? "pointer" : undefined }}
      data-testid="draft-card"
      onClick={onEdit}
      title={onEdit ? "Click to edit draft" : undefined}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={draftChipStyle}>Draft</span>
        {draft.to && (
          <span style={{ fontSize: 12, color: "#666" }}>to {draft.to}</span>
        )}
        {onEdit && (
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#c5221f", fontWeight: 500 }}>Edit ›</span>
        )}
      </header>
      {draft.subject && (
        <div style={{ fontWeight: 600, marginBottom: 6 }}>{draft.subject}</div>
      )}
      {draft.body ? (
        <pre style={draftBodyStyle}>{draft.body}</pre>
      ) : (
        <div style={mutedStyle}>(empty draft)</div>
      )}
      {atts.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {atts.map((a, i) => (
            <div
              key={i}
              style={{ ...attachmentChipStyle, cursor: onPreview && isPreviewable(a.mime_type) ? "pointer" : "default" }}
              onClick={(e) => {
                if (onPreview && isPreviewable(a.mime_type)) {
                  e.stopPropagation();
                  onPreview(a);
                }
              }}
              title={isPreviewable(a.mime_type) ? "Click to preview" : a.filename || ""}
            >
              <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
              <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.filename}
              </span>
              {a.size != null && (
                <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                  {formatFileSize(a.size)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
