// Styles for the inline composer (Gmail Material-3 compose card + threaded
// conversation panel).
import type { ComposerSaveStatus } from "./types";

export const composerBackBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  fontSize: 13,
  padding: "8px 0",
  marginBottom: 4,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const composerSubjectStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 400,
  color: "#202124",
  margin: "0 0 16px 0",
  lineHeight: 1.3,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const composerThreadPanelStyle: React.CSSProperties = {
  marginBottom: 8,
};

export const composerCardStyle: React.CSSProperties = {
  borderRadius: 8,
  border: "1px solid #dadce0",
  boxShadow: "0 1px 2px 0 rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15)",
  background: "#fff",
  overflow: "hidden",
  marginTop: 8,
};

export const composerFieldRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "8px 16px",
  borderBottom: "1px solid #eceff1",
  gap: 0,
};

export const composerFieldLabel: React.CSSProperties = {
  width: 36,
  color: "#5f6368",
  fontSize: 14,
  flexShrink: 0,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const composerInputStyle: React.CSSProperties = {
  flex: 1,
  padding: "4px 0",
  border: "none",
  outline: "none",
  fontSize: 14,
  color: "#202124",
  background: "transparent",
  fontFamily: "Roboto, Arial, sans-serif",
};

export const composerCcBccToggle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  fontSize: 13,
  cursor: "pointer",
  flexShrink: 0,
  padding: "2px 4px",
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const composerFieldDivider: React.CSSProperties = {
  borderBottom: "1px solid #dadce0",
};

export const composerTextareaStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 200,
  padding: "12px 16px",
  border: "none",
  outline: "none",
  fontSize: 14,
  lineHeight: "20px",
  color: "#202124",
  fontFamily: "Arial, Helvetica, sans-serif",
  boxSizing: "border-box",
  resize: "vertical",
  background: "transparent",
};

export const composerToolbarStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "4px 12px 8px 12px",
  borderTop: "1px solid #dadce0",
};

export const composerToolbarLeft: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
};

export const composerToolbarRight: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

export const composerSendBtnStyle: React.CSSProperties = {
  background: "#0b57d0",
  color: "#fff",
  border: "none",
  padding: "8px 24px",
  borderRadius: 18,
  cursor: "pointer",
  fontSize: 14,
  fontWeight: 500,
  fontFamily: "'Google Sans', Roboto, sans-serif",
  lineHeight: "20px",
  minHeight: 36,
};

export const composerToolbarIconBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  padding: 6,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
};

export const composerTrashBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  padding: 6,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  transition: "color 0.15s, background 0.15s",
};

export const composerSentStyle: React.CSSProperties = {
  background: "#e6f4ea",
  padding: "12px 16px",
  borderRadius: 8,
  color: "#137333",
  textAlign: "center",
  fontSize: 14,
  fontWeight: 500,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export function composerSaveStatusStyle(s: ComposerSaveStatus): React.CSSProperties {
  const base: React.CSSProperties = { fontSize: 11, fontFamily: "Roboto, Arial, sans-serif" };
  if (s.kind === "error") return { ...base, color: "#d93025" };
  if (s.kind === "saved") return { ...base, color: "#188038" };
  return { ...base, color: "#5f6368" };
}

export const composerAgentBanner: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 12px",
  background: "#fef7e0",
  border: "1px solid #fdd663",
  borderRadius: 8,
  fontSize: 13,
  color: "#3c4043",
  marginBottom: 8,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const composerAgentApplyBtn: React.CSSProperties = {
  background: "#1a73e8",
  color: "#fff",
  border: "none",
  borderRadius: 14,
  padding: "4px 14px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "'Google Sans', Roboto, sans-serif",
};

export const composerAgentKeepBtn: React.CSSProperties = {
  background: "none",
  color: "#5f6368",
  border: "1px solid #dadce0",
  borderRadius: 14,
  padding: "4px 14px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "'Google Sans', Roboto, sans-serif",
};

export const attachmentRemoveBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  marginLeft: 4,
  padding: "0 2px",
  fontSize: 14,
  lineHeight: 1,
  borderRadius: "50%",
};

export const composerCollapsedMsgStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 12px",
  borderBottom: "1px solid #eceff1",
  cursor: "pointer",
  background: "#fff",
  borderRadius: 0,
};

export const composerExpandedMsgStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderBottom: "1px solid #eceff1",
  background: "#fff",
};

export const composerBodyHtmlStyle: React.CSSProperties = {
  fontSize: 14,
  color: "#202124",
  lineHeight: 1.6,
  overflowX: "auto",
  wordBreak: "break-word",
};

export const composerBodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "Arial, Helvetica, sans-serif",
  margin: 0,
  fontSize: 14,
  color: "#202124",
  lineHeight: 1.6,
};

export const composerQuoteToggle: React.CSSProperties = {
  display: "inline-block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 10px",
  fontSize: 13,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 6,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};
