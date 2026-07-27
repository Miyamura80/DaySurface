import type { SaveStatus } from "./types";

export const containerStyle: React.CSSProperties = {
  fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
  padding: 16,
  maxWidth: 720,
  color: "#111",
};

export const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 12,
};

export const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  marginBottom: 8,
};

export const labelStyle: React.CSSProperties = {
  width: 70,
  color: "#555",
  fontSize: 13,
};

export const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 8px",
  border: "1px solid #ddd",
  borderRadius: 4,
  fontSize: 14,
  width: "100%",
  boxSizing: "border-box",
};

export const readOnlyStyle: React.CSSProperties = {
  padding: "6px 8px",
  color: "#666",
  fontSize: 14,
};

export const textareaStyle: React.CSSProperties = {
  width: "100%",
  padding: 8,
  border: "1px solid #ddd",
  borderRadius: 4,
  fontSize: 14,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  marginTop: 8,
  boxSizing: "border-box",
};

// Mobile: the box auto-grows to its content (see the effect in Composer), so
// there is no inner scroll to trap a finger drag - the page scrolls instead.
// `touchAction: manipulation` and a comfortable min-height round it out.
export const mobileTextareaStyle: React.CSSProperties = {
  ...textareaStyle,
  minHeight: 180,
  overflowY: "hidden",
  resize: "none",
  fontSize: 16, // prevents iOS Safari from zooming in on focus
  touchAction: "manipulation",
};

export const buttonRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 12,
  alignItems: "center",
  flexWrap: "wrap",
};

export const primaryButtonStyle: React.CSSProperties = {
  background: "#3b82f6",
  color: "white",
  border: "none",
  padding: "6px 14px",
  borderRadius: 6,
  cursor: "pointer",
};

export const secondaryButtonStyle: React.CSSProperties = {
  background: "#f3f4f6",
  color: "#111",
  border: "1px solid #ddd",
  padding: "6px 12px",
  borderRadius: 6,
  cursor: "pointer",
};

export const destructiveButtonStyle: React.CSSProperties = {
  background: "transparent",
  color: "#991b1b",
  border: "1px solid #fecaca",
  padding: "6px 12px",
  borderRadius: 6,
  cursor: "pointer",
};

export const linkButtonStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#3b82f6",
  padding: 0,
  cursor: "pointer",
  fontSize: 13,
};

export const smallPrimaryStyle: React.CSSProperties = {
  ...primaryButtonStyle,
  padding: "2px 8px",
  fontSize: 12,
};

export const smallSecondaryStyle: React.CSSProperties = {
  ...secondaryButtonStyle,
  padding: "2px 8px",
  fontSize: 12,
};

export const confirmRowStyle: React.CSSProperties = {
  display: "inline-flex",
  gap: 8,
  alignItems: "center",
  fontSize: 13,
  color: "#991b1b",
};

export const agentBannerStyle: React.CSSProperties = {
  background: "#fff7ed",
  border: "1px solid #fed7aa",
  padding: "6px 10px",
  borderRadius: 6,
  marginBottom: 8,
  display: "flex",
  gap: 8,
  alignItems: "center",
  fontSize: 13,
};

export const successStyle: React.CSSProperties = {
  background: "#ecfdf5",
  border: "1px solid #a7f3d0",
  padding: "10px 12px",
  borderRadius: 6,
  color: "#065f46",
};

export const mutedStyle: React.CSSProperties = {
  color: "#666",
};

export const threadPanelStyle: React.CSSProperties = {
  borderBottom: "1px solid #e5e7eb",
  marginBottom: 12,
  paddingBottom: 8,
};

export const threadToggleBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  padding: "4px 0",
  cursor: "pointer",
  fontSize: 13,
  color: "#374151",
  fontWeight: 600,
};

export const threadMessagesContainer: React.CSSProperties = {
  maxHeight: 280,
  overflowY: "auto",
  WebkitOverflowScrolling: "touch",
  marginTop: 6,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

// Mobile: drop the fixed-height inner scroll region. Nested scrolling inside
// the iframe is the thing that feels broken under touch, so let the messages
// flow and the whole page scroll instead.
export const mobileThreadMessagesContainer: React.CSSProperties = {
  ...threadMessagesContainer,
  maxHeight: "none",
  overflowY: "visible",
};

export const threadMsgCollapsedStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 8px",
  borderRadius: 4,
  background: "#f9fafb",
  cursor: "pointer",
  border: "1px solid #f3f4f6",
};

export const threadMsgExpandedStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 4,
  background: "#f9fafb",
  border: "1px solid #e5e7eb",
};

export const threadBodyHtmlStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#374151",
  lineHeight: 1.5,
  wordBreak: "break-word",
};

export const threadBodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 12,
  color: "#374151",
};

export const quoteToggleBtnStyle: React.CSSProperties = {
  display: "block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 10px",
  fontSize: 12,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 4,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};

export function statusStyle(s: SaveStatus): React.CSSProperties {
  if (s.kind === "error") return { color: "#991b1b", fontSize: 12 };
  if (s.kind === "saved") return { color: "#059669", fontSize: 12 };
  return { color: "#666", fontSize: 12 };
}
