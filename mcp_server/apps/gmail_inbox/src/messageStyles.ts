// Styles for the message reader, attachments, draft card, PDF viewer, and the
// shared attachment-preview modal.

export const collapsedMessageStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  border: "1px solid #eee",
  borderRadius: 6,
  padding: "8px 12px",
  marginBottom: 4,
  background: "#f8f9fa",
  cursor: "pointer",
  color: "#202124",
};

export const collapsedReplyBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#666",
  cursor: "pointer",
  padding: 4,
  borderRadius: 4,
  display: "inline-flex",
  alignItems: "center",
  flexShrink: 0,
};

export const messageStyle: React.CSSProperties = {
  border: "1px solid #eee",
  borderRadius: 6,
  padding: 12,
  marginBottom: 10,
  background: "#fafbfc",
  color: "#202124",
};

export const messageHeaderStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  marginBottom: 8,
  fontSize: 13,
};

export const messageActionsStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 10,
  paddingTop: 8,
  borderTop: "1px solid #eee",
};

export const messageActionBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  background: "#fff",
  border: "1px solid #dadce0",
  borderRadius: 16,
  padding: "5px 14px",
  fontSize: 12,
  color: "#444",
  cursor: "pointer",
};

export const bodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 13,
  color: "#222",
};

export const bodyHtmlStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#222",
  overflowX: "auto",
  lineHeight: 1.5,
  wordBreak: "break-word",
};

export const quoteToggleStyle: React.CSSProperties = {
  display: "block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 12px",
  fontSize: 14,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 6,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};

export const draftCardStyle: React.CSSProperties = {
  border: "2px dashed #c5221f",
  borderRadius: 6,
  padding: 12,
  marginBottom: 10,
  background: "#fef7f6",
  color: "#202124",
};

export const draftBodyStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 13,
  color: "#444",
};

export const imageAttachmentStyle: React.CSSProperties = {
  background: "#f8f9fa",
  border: "1px solid #e0e0e0",
  borderRadius: 8,
  padding: "8px 12px",
  minWidth: 120,
};

export const attachmentsRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  marginTop: 10,
};

export const attachmentChipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  background: "#f1f3f4",
  border: "1px solid #dadce0",
  borderRadius: 16,
  padding: "4px 10px",
  fontSize: 12,
  color: "#3c4043",
};

export const previewOverlayStyle: React.CSSProperties = {
  position: "fixed",
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  background: "rgba(0,0,0,0.5)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

export const previewModalStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  width: "90%",
  maxWidth: 800,
  height: "80%",
  maxHeight: 600,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  boxShadow: "0 8px 32px rgba(0,0,0,0.24)",
};

export const previewHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "12px 16px",
  borderBottom: "1px solid #e0e0e0",
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const previewCloseBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  fontSize: 20,
  cursor: "pointer",
  color: "#5f6368",
  padding: "4px 8px",
  borderRadius: "50%",
  lineHeight: 1,
};

export const previewBodyStyle: React.CSSProperties = {
  flex: 1,
  overflow: "auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#f8f9fa",
};

export const pdfNavBtn: React.CSSProperties = {
  background: "none",
  border: "1px solid #dadce0",
  borderRadius: 4,
  padding: "4px 8px",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
};
