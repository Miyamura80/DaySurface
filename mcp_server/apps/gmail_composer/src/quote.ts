export function splitHtmlAtQuote(html: string): { main: string; quoted: string | null } {
  const markers = [
    '<div class="gmail_quote"',
    "<div class=\"gmail_quote\"",
    '<blockquote class="gmail_quote"',
    "<blockquote class=\"gmail_quote\"",
    '<div class=3D"gmail_quote"',
  ];
  for (const marker of markers) {
    const idx = html.indexOf(marker);
    if (idx > 0) return { main: html.slice(0, idx), quoted: html.slice(idx) };
  }
  const onWroteRe = /(<br\s*\/?>[\s\S]{0,20}?On\s.{10,80}\s+wrote:\s*<br\s*\/?>)/i;
  const m = onWroteRe.exec(html);
  if (m && m.index > 50) return { main: html.slice(0, m.index), quoted: html.slice(m.index) };
  return { main: html, quoted: null };
}

export function splitTextAtQuote(text: string): { main: string; quoted: string | null } {
  const lines = text.split("\n");
  const onWroteRe = /^On .{10,80} wrote:\s*$/;
  for (let i = 0; i < lines.length; i++) {
    if (onWroteRe.test(lines[i]) && i > 0) {
      return { main: lines.slice(0, i).join("\n"), quoted: lines.slice(i).join("\n") };
    }
  }
  let firstQuoteLine = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith(">")) {
      if (firstQuoteLine === -1) firstQuoteLine = i;
    } else if (firstQuoteLine !== -1) {
      break;
    }
  }
  if (firstQuoteLine > 0 && lines.length - firstQuoteLine >= 3) {
    return { main: lines.slice(0, firstQuoteLine).join("\n"), quoted: lines.slice(firstQuoteLine).join("\n") };
  }
  return { main: text, quoted: null };
}

export function relativeTime(iso: string | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const ageMs = Date.now() - dt.getTime();
  const mins = Math.round(ageMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return dt.toLocaleDateString();
}
