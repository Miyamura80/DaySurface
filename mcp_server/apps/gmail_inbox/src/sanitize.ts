import DOMPurify from "dompurify";
import type { Config } from "dompurify";

/**
 * Shared DOMPurify config for untrusted email HTML. Exported so
 * `sanitizeEmailHtml` (remoteImages.ts) runs the exact same profile with a
 * remote-image-blocking hook attached - the two must never drift.
 */
export const EMAIL_SANITIZE_CONFIG: Config = {
  // HTML profile only - drop SVG/MathML to shrink the attack surface; email
  // bodies do not legitimately need them.
  USE_PROFILES: { html: true },
  // Allow links to declare their target (e.g. _blank) without re-enabling
  // event handlers.
  ADD_ATTR: ["target"],
};

/**
 * Sanitize untrusted email HTML before injecting it via
 * `dangerouslySetInnerHTML`. External senders fully control this markup, so we
 * strip `<script>`, inline event handlers (`onerror`, `onclick`, ...) and
 * unsafe URI schemes (`javascript:`, `data:` on links) while preserving the
 * formatting, tables, images and inline styles that real emails rely on.
 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, EMAIL_SANITIZE_CONFIG);
}
