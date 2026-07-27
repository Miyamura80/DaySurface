// Shell, list, and reader-frame styles. Message/attachment styles live in
// messageStyles.ts; composer styles in composerStyles.ts.

export const appStyle: React.CSSProperties = {
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  display: "flex",
  height: "min(100vh, 480px)",
  maxHeight: 480,
  width: "100%",
  color: "#202124",
  background: "#fff",
  borderRadius: 8,
  overflow: "hidden",
  colorScheme: "light",
};

// Single-column variant for narrow viewports (phone-sized MCP clients).
//
// On mobile the app is a sandboxed iframe embedded inside the host chat's own
// scroll view. Trapping scroll inside a fixed-height shell with nested
// `overflow: auto` panes (the wide-screen approach) does not work there:
// touch-dragging a nested scroll region inside a sandboxed iframe is
// unreliable - the gesture is swallowed by the outer page, so the inbox looks
// frozen. Instead we let the shell grow to its natural content height and let
// the host page scroll the iframe, the standard pattern for embedded mobile
// iframe UIs. So: no height cap and `overflow: visible` everywhere on the
// narrow path.
export const appStyleNarrow: React.CSSProperties = {
  ...appStyle,
  height: "auto",
  maxHeight: "none",
  overflow: "visible",
};

export const listPaneStyle: React.CSSProperties = {
  width: "38%",
  borderRight: "1px solid #e0e0e0",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

export const listPaneNarrowStyle: React.CSSProperties = {
  ...listPaneStyle,
  width: "100%",
  borderRight: "none",
  overflow: "visible",
};

// Sticky top app bar shown above the reader on narrow screens, mirroring the
// Gmail mobile "back to inbox" affordance.
export const readerTopBarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "6px 8px",
  borderBottom: "1px solid #e0e0e0",
  flexShrink: 0,
  background: "#fff",
};

export const readerBackBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  background: "none",
  border: "none",
  color: "#1a73e8",
  cursor: "pointer",
  fontSize: 14,
  fontWeight: 500,
  padding: "4px 8px",
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

export const listHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "10px 12px",
  borderBottom: "1px solid #eee",
  color: "#202124",
};

export const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  overflowY: "auto",
  WebkitOverflowScrolling: "touch",
  overscrollBehavior: "contain",
  flex: 1,
};

// Narrow path: the list flows at its natural height and the host page scrolls,
// so it must not trap scroll inside a flex-sized `overflow: auto` box (which is
// unreliable to touch-scroll inside a mobile iframe).
export const listStyleNarrow: React.CSSProperties = {
  ...listStyle,
  overflowY: "visible",
  flex: "none",
};

export const rowStyle: React.CSSProperties = {
  padding: "10px 12px",
  borderBottom: "1px solid #f0f0f0",
  cursor: "pointer",
  color: "#202124",
};

export const rowTopStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

export const rowMidStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#555",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export const rowSnippetStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#888",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export const rowFootStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#999",
  marginTop: 4,
};

export const chipStyle: React.CSSProperties = {
  background: "#f1f3f4",
  color: "#444",
  fontSize: 11,
  padding: "2px 6px",
  borderRadius: 10,
  flexShrink: 0,
};

export const labelChipsRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
  marginTop: 4,
};

export const labelChipBaseStyle: React.CSSProperties = {
  fontSize: 10,
  padding: "1px 6px",
  borderRadius: 8,
  whiteSpace: "nowrap",
  fontWeight: 500,
};

export const reasonChipStyle: React.CSSProperties = {
  ...labelChipBaseStyle,
  background: "#f1f3f4",
  color: "#5f6368",
};

export const draftChipStyle: React.CSSProperties = {
  ...labelChipBaseStyle,
  background: "#fce8e6",
  color: "#c5221f",
  fontWeight: 600,
  display: "inline-flex",
  alignItems: "center",
  gap: 3,
};

export const draftDiscardBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#c5221f",
  cursor: "pointer",
  padding: 0,
  fontSize: 13,
  fontWeight: 700,
  lineHeight: 1,
  display: "inline-flex",
  alignItems: "center",
  opacity: 0.6,
  marginLeft: 2,
};

export const replyContextInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  border: "1px solid #dadce0",
  borderRadius: 8,
  fontSize: 13,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
  color: "#202124",
  resize: "vertical",
  outline: "none",
  boxSizing: "border-box",
};

export const readerPaneStyle: React.CSSProperties = {
  flex: 1,
  padding: 16,
  overflowY: "auto",
  WebkitOverflowScrolling: "touch",
  overscrollBehavior: "contain",
  color: "#202124",
};

// Narrow path: let the reader flow at its natural height so the host page
// scrolls the iframe, rather than trapping scroll in a nested pane that mobile
// webviews struggle to touch-scroll.
export const readerPaneNarrowStyle: React.CSSProperties = {
  ...readerPaneStyle,
  overflowY: "visible",
  flex: "none",
};

export const actionsStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  marginBottom: 8,
};

export const iconBtnStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #dadce0",
  color: "#5f6368",
  padding: 6,
  borderRadius: 8,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  lineHeight: 1,
};

export const mutedStyle: React.CSSProperties = {
  color: "#777",
  fontSize: 13,
  padding: 8,
};

export const statusStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "6px 10px",
  background: "#e6f4ea",
  border: "1px solid #b6e0c2",
  borderRadius: 6,
  color: "#137333",
  fontSize: 12,
};

export const errorStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "6px 10px",
  background: "#fce8e6",
  border: "1px solid #f5c6c2",
  borderRadius: 6,
  color: "#b3261e",
  fontSize: 12,
};

// Scoped CSS for the animated "Quick reply" / "Deep context reply" buttons in
// the reader. Injected via a <style> tag because it needs pseudo-elements and
// keyframes that inline styles can't express.
export const aiReplyStyles = `
    .fast-reply-btn {
      display: flex; align-items: center; justify-content: center; gap: 6px;
      flex: 1; padding: 12px 16px; border: 1px solid rgba(6,182,212,0.3); border-radius: 10px;
      color: #0e7490; font-size: 14px; font-weight: 600; cursor: pointer;
      background: linear-gradient(135deg, #ecfeff, #cffafe);
      position: relative; overflow: hidden;
      transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
    }
    .fast-reply-btn:hover {
      transform: translateY(-1px);
      border-color: rgba(6,182,212,0.5);
      box-shadow: 0 2px 12px rgba(6,182,212,0.2);
    }
    .fast-reply-btn::before {
      content: ""; position: absolute; top: 0; left: -100%; width: 200%; height: 100%;
      background: linear-gradient(90deg, transparent 0%, rgba(6,182,212,0.08) 50%, transparent 100%);
      animation: ai-shimmer 3s ease-in-out infinite;
    }
    .fast-sparkle { color: #06b6d4; animation: ai-twinkle 2.2s ease-in-out infinite; }
    .ai-reply-btn {
      display: flex; align-items: center; justify-content: center; gap: 8px;
      flex: 1.2; padding: 12px 16px; border: none; border-radius: 10px;
      color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
      position: relative; overflow: hidden;
      background: linear-gradient(135deg, #0891b2, #06b6d4, #22d3ee);
      box-shadow: 0 2px 12px rgba(6,182,212,0.4);
      transition: transform 0.15s, box-shadow 0.15s;
    }
    .ai-reply-btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 20px rgba(6,182,212,0.55);
    }
    .ai-reply-btn::before {
      content: ""; position: absolute; top: 0; left: -100%; width: 200%; height: 100%;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.2) 50%, transparent 100%);
      animation: ai-shimmer 2.5s ease-in-out infinite;
    }
    @keyframes ai-shimmer {
      0% { left: -100%; }
      100% { left: 100%; }
    }
    .ai-sparkle { animation: ai-twinkle 1.8s ease-in-out infinite; }
    .ai-sparkle-sm { animation-delay: 0.6s; }
    @keyframes ai-twinkle {
      0%, 100% { opacity: 0.7; transform: scale(1) rotate(0deg); }
      50% { opacity: 1; transform: scale(1.2) rotate(15deg); }
    }
  `;
