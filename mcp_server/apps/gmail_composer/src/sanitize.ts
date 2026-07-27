import DOMPurify from "dompurify";

/**
 * Sanitize untrusted email HTML before injecting it via
 * `dangerouslySetInnerHTML`. External senders fully control this markup, so we
 * strip `<script>`, inline event handlers (`onerror`, `onclick`, ...) and
 * unsafe URI schemes (`javascript:`, `data:` on links) while preserving the
 * formatting, tables, images and inline styles that real emails rely on.
 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    // HTML profile only - drop SVG/MathML to shrink the attack surface; email
    // bodies do not legitimately need them.
    USE_PROFILES: { html: true },
    // Allow links to declare their target (e.g. _blank) without re-enabling
    // event handlers.
    ADD_ATTR: ["target"],
  });
}
