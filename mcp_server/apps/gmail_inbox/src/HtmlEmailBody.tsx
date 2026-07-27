/**
 * Renders sanitized email HTML with remote images blocked by default and a
 * Gmail-style "Show images" action.
 *
 * Blocking + sanitization happen in one DOMPurify pass (see remoteImages.ts
 * for the why). On "Show images" each unresolved URL is fetched server-side
 * through the app-only `gmail_inbox.fetch_image` tool and the HTML is
 * re-derived from the ORIGINAL untrusted source with the resolved map - the
 * transform never re-ingests its own output.
 *
 * State model: `resolved === null` means nothing fetched yet; a Map (possibly
 * with misses) accumulates fetched data: URIs across clicks. The banner stays
 * visible while any URL remains unresolved, so failures and the per-click
 * fetch cap surface as a "Retry" instead of a silent dead end. A sequence ref
 * cancels in-flight fetches when `html` changes under the same component
 * instance (the lean-thread -> full-thread upgrade does exactly that).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { extractStructuredContent, splitHtmlAtQuote } from "./helpers";
import { sanitizeEmailHtml } from "./remoteImages";

/** Minimal slice of the ext-apps App surface this component needs. */
export type ServerToolCaller = {
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
};

// Cap per-click proxy fetches so a pathological newsletter can't fan out
// into hundreds of parallel gmail_inbox.fetch_image calls. Remaining images
// stay blocked and the banner offers another round.
const MAX_REMOTE_IMAGES_PER_FETCH = 20;

export function HtmlEmailBody({
  html,
  mcpApp,
  htmlStyle,
  quoteToggleStyle,
  onClick,
}: {
  html: string;
  mcpApp: ServerToolCaller;
  htmlStyle: React.CSSProperties;
  quoteToggleStyle: React.CSSProperties;
  onClick?: (e: React.MouseEvent<HTMLElement>) => void;
}) {
  const [showQuoted, setShowQuoted] = useState(false);
  // The resolved map is KEYED to the html that produced it: when the html
  // prop swaps (lean -> full thread upgrade), the very first render of the
  // new content must start fully blocked. An effect-based reset runs a frame
  // too late and would flash previously-approved images without consent.
  const [resolvedState, setResolvedState] = useState<{
    forHtml: string;
    map: Map<string, string>;
  } | null>(null);
  const resolved = resolvedState?.forHtml === html ? resolvedState.map : null;
  const [loadingImages, setLoadingImages] = useState(false);
  // Bumped whenever html changes; in-flight fetches compare against it and
  // drop their result instead of resurrecting state for replaced content.
  const fetchSeqRef = useRef(0);

  useEffect(() => {
    fetchSeqRef.current++;
    // Free the stale map (the render guard above already ignores it).
    setResolvedState((cur) => (cur?.forHtml === html ? cur : null));
    setLoadingImages(false);
  }, [html]);

  const { main, quoted } = useMemo(() => splitHtmlAtQuote(html), [html]);
  const mainOut = useMemo(() => sanitizeEmailHtml(main, resolved), [main, resolved]);
  const quotedOut = useMemo(
    () => (quoted ? sanitizeEmailHtml(quoted, resolved) : null),
    [quoted, resolved],
  );
  const unresolvedUrls = useMemo(() => {
    const all = new Set([...mainOut.remoteUrls, ...(quotedOut?.remoteUrls ?? [])]);
    return [...all].filter((u) => !resolved?.has(u));
  }, [mainOut, quotedOut, resolved]);

  const showImages = async () => {
    const seq = fetchSeqRef.current;
    setLoadingImages(true);
    const entries = await Promise.all(
      unresolvedUrls.slice(0, MAX_REMOTE_IMAGES_PER_FETCH).map(
        async (url): Promise<[string, string] | null> => {
          try {
            const raw = await mcpApp.callServerTool({
              name: "gmail_inbox.fetch_image",
              arguments: { url },
            });
            const parsed = extractStructuredContent<{
              mime_type?: string;
              data_base64?: string;
            }>(raw);
            if (parsed?.data_base64 && parsed.mime_type?.startsWith("image/")) {
              return [url, `data:${parsed.mime_type};base64,${parsed.data_base64}`];
            }
          } catch {
            // Per-image best effort: failures stay blocked; banner offers Retry.
          }
          return null;
        },
      ),
    );
    if (seq !== fetchSeqRef.current) return; // html changed mid-fetch
    setResolvedState((prev) => {
      const next = new Map(prev?.forHtml === html ? prev.map : []);
      for (const entry of entries) if (entry) next.set(entry[0], entry[1]);
      return { forHtml: html, map: next };
    });
    setLoadingImages(false);
  };

  return (
    <div>
      {unresolvedUrls.length > 0 && (
        <div style={showImagesBannerStyle} data-testid="show-images-banner">
          <span>Remote images are hidden ({unresolvedUrls.length}).</span>
          <button
            onClick={showImages}
            style={showImagesBtnStyle}
            disabled={loadingImages}
            data-testid="show-images-btn"
          >
            {loadingImages ? "Loading…" : resolved === null ? "Show images" : "Retry"}
          </button>
        </div>
      )}
      <div
        style={htmlStyle}
        dangerouslySetInnerHTML={{ __html: mainOut.html }}
        onClick={onClick}
      />
      {quotedOut !== null && (
        <>
          <button
            onClick={() => setShowQuoted((v) => !v)}
            style={quoteToggleStyle}
            title={showQuoted ? "Hide quoted text" : "Show quoted text"}
          >
            •••
          </button>
          {showQuoted && (
            <div
              style={{ ...htmlStyle, borderLeft: "3px solid #dadce0", paddingLeft: 10, marginTop: 4 }}
              dangerouslySetInnerHTML={{ __html: quotedOut.html }}
              onClick={onClick}
            />
          )}
        </>
      )}
    </div>
  );
}

const showImagesBannerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  padding: "6px 10px",
  marginBottom: 8,
  background: "#f8f9fa",
  border: "1px solid #ebebeb",
  borderRadius: 6,
  fontSize: 12,
  color: "#5f6368",
};

const showImagesBtnStyle: React.CSSProperties = {
  border: "1px solid #dadce0",
  borderRadius: 4,
  background: "#fff",
  padding: "3px 10px",
  fontSize: 12,
  fontWeight: 600,
  color: "#1a73e8",
  cursor: "pointer",
  flexShrink: 0,
};
