import { describe, expect, it } from "vitest";
import { sanitizeHtml } from "./sanitize";

describe("sanitizeHtml", () => {
  it("removes <script> tags from untrusted email HTML", () => {
    const out = sanitizeHtml("<p>hello</p><script>alert(1)</script>");
    expect(out).toContain("<p>hello</p>");
    expect(out.toLowerCase()).not.toContain("<script");
  });

  it("strips inline event-handler attributes", () => {
    const out = sanitizeHtml('<img src="x" onerror="alert(1)">');
    expect(out).toContain("<img");
    expect(out.toLowerCase()).not.toContain("onerror");
  });

  it("drops javascript: URI schemes on links", () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">click</a>');
    expect(out).toContain("click");
    expect(out.toLowerCase()).not.toContain("javascript:");
  });

  it("preserves safe formatting, inline styles and links", () => {
    const out = sanitizeHtml(
      '<p style="color:red">hi <a href="https://example.com" target="_blank">link</a></p>',
    );
    expect(out).toContain('style="color:red"');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('target="_blank"');
  });

  it("preserves tables that email layouts depend on", () => {
    const out = sanitizeHtml(
      '<table><tr><td style="padding:4px">cell</td></tr></table>',
    );
    expect(out).toContain("<td");
    expect(out).toContain("cell");
  });
});
