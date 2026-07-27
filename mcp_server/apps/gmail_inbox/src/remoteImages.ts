/**
 * Sanitize email HTML with remote images blocked (or resolved) in the SAME
 * DOMPurify pass.
 *
 * Why one pass matters: DOMPurify's safety guarantee applies to the exact
 * string it emits. Any later parse -> mutate -> serialize step (the mXSS
 * problem class) would make us the last writer instead of the sanitizer, so
 * the blocking runs inside an `afterSanitizeAttributes` hook and the string
 * handed to `dangerouslySetInnerHTML` is byte-for-byte DOMPurify output.
 *
 * Why blocking at all: strict-CSP hosts (claude.ai enforces `img-src 'self'
 * data: blob: https://assets.claude.ai` on the app iframe) block every image
 * an email references from a sender-controlled origin, while lax hosts
 * (Goose) load them straight from the network. We normalize both to classic
 * email-client behavior: remote references are stripped at render time and a
 * "Show images" action fetches them through the server
 * (`gmail_inbox.fetch_image`) and re-renders with `data:` URIs, which every
 * host CSP allows. This also stops tracking pixels firing without user
 * intent on lax hosts.
 *
 * The blocking is DEFAULT-DENY, not an enumerated blocklist: on every
 * element, every URL-bearing attribute DOMPurify's HTML profile can admit
 * (`src`, `poster`, `background`, `srcset`, plus `url()` in inline styles)
 * keeps only `data:`/`cid:` values. Everything else - absolute http(s),
 * protocol-relative `//host/...`, relative paths, unknown schemes - is
 * stripped, so a new URL-bearing attribute admitted by a future DOMPurify
 * profile change is blocked here for free instead of becoming a bypass.
 */

import DOMPurify from "dompurify";
import { EMAIL_SANITIZE_CONFIG } from "./sanitize";

/** 1x1 transparent GIF shown in place of a blocked remote image. */
export const BLOCKED_IMG_PLACEHOLDER =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

/** Values that never leave the document and are allowed by every host CSP. */
const SAFE_INLINE_RE = /^(data|cid):/i;

/** Absolute or protocol-relative remote reference - fetchable via the proxy. */
const REMOTE_RE = /^(https?:)?\/\//i;

/**
 * Attributes that trigger a fetch when the HTML renders. `srcset` is handled
 * separately (multi-URL syntax; always stripped, `src` covers restore).
 */
const URL_ATTRS = ["src", "poster", "background"] as const;

/**
 * Elements whose remote reference is an IMAGE the proxy can fetch and
 * restore. Other remote media (audio/video/track src) is stripped but NOT
 * recorded in `remoteUrls` - feeding it to the image-only proxy would just
 * inflate the banner count with permanently-unfetchable entries.
 */
function isRestorable(node: Element, attr: string): boolean {
  if (attr === "background") return true;
  if (attr === "poster") return node.tagName === "VIDEO";
  return (
    attr === "src" &&
    (node.tagName === "IMG" ||
      (node.tagName === "INPUT" &&
        node.getAttribute("type")?.toLowerCase() === "image"))
  );
}

/**
 * Remove external-fetch vectors from an inline style via the CSSOM, not the
 * raw attribute string. Raw-string regexes are bypassable with CSS escapes
 * (`u\72l(...)` parses as `url(...)`), so we first re-serialize any
 * escape-bearing style through the CSS parser (which resolves escapes), then
 * walk the parsed declarations and drop every property whose value carries a
 * non-`data:` `url()`, an `image-set()`, or a residual escape.
 *
 * Deliberate non-goal: CSS background URLs are stripped, not collected for
 * "Show images" restore - real email clients rarely honor them and keeping
 * them out of `remoteUrls` avoids style-rebuild complexity.
 */
function scrubStyle(node: Element): void {
  const raw = node.getAttribute("style");
  if (!raw) return;
  const style = (node as HTMLElement).style;
  if (!style) {
    node.removeAttribute("style");
    return;
  }
  // Walk the PARSED declarations: escapes are resolved by the CSS parser, so
  // `u\72l(...)` shows up here as `url(...)` and gets dropped.
  for (let i = style.length - 1; i >= 0; i--) {
    const prop = style.item(i);
    const value = style.getPropertyValue(prop);
    const urlRefs = value.match(/url\(\s*['"]?[^'")]*/gi) || [];
    const hasRemoteUrl = urlRefs.some((u) => !/^url\(\s*['"]?data:/i.test(u));
    if (hasRemoteUrl || /image-set\(/i.test(value) || value.includes("\\")) {
      style.removeProperty(prop);
    }
  }
  // Re-serialize from the CSSOM unconditionally: any declaration the parser
  // could not represent (and the walk therefore could not inspect) is dropped
  // rather than passed through as raw attacker-controlled text.
  const clean = style.cssText;
  if (clean) node.setAttribute("style", clean);
  else node.removeAttribute("style");
}

export type SanitizedEmailHtml = {
  /** Sanitized HTML, remote references blocked or swapped to data: URIs. */
  html: string;
  /**
   * Unique remote URLs found (protocol-relative normalized to https:), in
   * document order - both still-blocked and already-resolved ones, so
   * callers can diff against `resolved` to know what remains hidden.
   */
  remoteUrls: string[];
};

/**
 * Sanitize untrusted email HTML, blocking remote image references.
 *
 * `resolved` maps remote URL -> `data:` URI previously fetched through the
 * server proxy; matching references are swapped in instead of blocked. Call
 * again with a bigger map to reveal more images - the transform always
 * starts from the original untrusted HTML, never from its own output.
 */
export function sanitizeEmailHtml(
  html: string,
  resolved?: Map<string, string> | null,
): SanitizedEmailHtml {
  const remoteUrls: string[] = [];
  const seen = new Set<string>();

  const hook = (node: Element) => {
    for (const attr of URL_ATTRS) {
      if (!node.hasAttribute(attr)) continue;
      const value = (node.getAttribute(attr) || "").trim();
      if (!value || SAFE_INLINE_RE.test(value)) continue;
      if (REMOTE_RE.test(value) && isRestorable(node, attr)) {
        const url = value.startsWith("//") ? `https:${value}` : value;
        if (!seen.has(url)) {
          seen.add(url);
          remoteUrls.push(url);
        }
        // This hook runs after DOMPurify's own attribute checks, so writes
        // here bypass its URI-scheme validation - accept nothing but the
        // data:image/* URIs the fetch flow produces.
        const dataUri = resolved?.get(url);
        if (dataUri && /^data:image\//i.test(dataUri)) {
          node.setAttribute(attr, dataUri);
          continue;
        }
      }
      // Default-deny: unresolved remote, non-image media, relative path, or
      // unknown scheme.
      if (attr === "src" && node.tagName === "IMG") {
        node.setAttribute("src", BLOCKED_IMG_PLACEHOLDER);
      } else {
        node.removeAttribute(attr);
      }
    }
    // srcset can carry many URLs (and lives on <source> inside <picture>,
    // where the browser prefers it over the blocked <img src>). Always drop
    // it; the plain src swap covers everything emails actually rely on.
    if (node.hasAttribute("srcset")) node.removeAttribute("srcset");

    scrubStyle(node);
  };

  DOMPurify.addHook("afterSanitizeAttributes", hook);
  try {
    return {
      html: DOMPurify.sanitize(html, EMAIL_SANITIZE_CONFIG),
      remoteUrls,
    };
  } finally {
    DOMPurify.removeHook("afterSanitizeAttributes");
  }
}
