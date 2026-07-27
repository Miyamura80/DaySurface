// pdf_signer MCP App - the human half of the signing ceremony.
//
// The host renders this iframe when pdf_request_signature locks a document.
// The user reviews every page (all pages render into one scrollable column,
// with the signature placement highlighted), types their full legal name,
// ticks the consent checkbox, and clicks Sign - which calls the app-only
// pdf_signer.sign tool. The model never sees this UI and cannot drive it;
// on elicitation-capable hosts the server additionally asks the host to
// show a native confirmation dialog before sealing.

import "./polyfills";
import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
// Static worker import: sandboxed host iframes (Goose's mcp-app-guest) block
// both spawning a Worker from the inlined data: URL AND the dynamic import
// pdf.js's "fake worker" fallback would do. Bundling the worker module and
// pre-seeding globalThis.pdfjsWorker makes the fallback synchronous-safe, so
// rendering works on the main thread wherever real workers are unavailable.
import * as pdfjsWorker from "pdfjs-dist/build/pdf.worker.min.mjs";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { styles } from "./styles";

(globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = pdfjsWorker;
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;

export type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
  openLink: (params: { url: string }) => Promise<unknown>;
  sendMessage?: (params: {
    role: string;
    content: { type: string; text: string }[];
  }) => Promise<unknown>;
};

export type SignerDocument = {
  doc_id: string;
  filename: string;
  status: "open" | "awaiting_signature" | "signed";
  page_count: number;
  stamp_page?: number | null;
  // Exact stamp footprint [x0, y0, x1, y1] in PDF user space, resolved
  // server-side so the highlight and the real stamp share one geometry.
  stamp_rect?: number[] | null;
  data_base64: string;
};

type SignOutcome = {
  status?: string;
  signed_by?: string;
  signed_at_utc?: string;
  message?: string;
};

type Phase =
  | "waiting"
  | "loading"
  | "review"
  | "signing"
  | "signed"
  | "cancelled"
  | "error";

// Same hardened CallToolResult parsing as the gmail apps (#183/#201): accept
// a structured object only from `structuredContent` or a real MCP TextContent
// block - never the raw envelope. Local copy pending the shared per-app
// helpers consolidation (issue #170).
function extractStructuredContent<T>(raw: unknown): T | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.structuredContent && typeof obj.structuredContent === "object") {
    return obj.structuredContent as T;
  }
  if (Array.isArray(obj.content)) {
    for (const item of obj.content) {
      if (!item || typeof item !== "object") continue;
      const candidate = item as { type?: unknown; text?: unknown };
      if (candidate.type !== "text" || typeof candidate.text !== "string") continue;
      try {
        const parsed = JSON.parse(candidate.text);
        if (parsed && typeof parsed === "object") return parsed as T;
      } catch { /* not JSON text content */ }
    }
  }
  return null;
}

type RenderedPage = {
  pageNo: number;
  url: string;
  cssWidth: number;
  cssHeight: number;
  scale: number;
  pdfHeight: number;
};

const PAGE_TARGET_WIDTH = 640;

async function renderAllPages(dataBase64: string): Promise<RenderedPage[]> {
  const bytes = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  const doc = await pdfjsLib.getDocument({ data: bytes, isEvalSupported: false })
    .promise;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pages: RenderedPage[] = [];
  for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
    const page = await doc.getPage(pageNo);
    const unscaled = page.getViewport({ scale: 1 });
    const scale = PAGE_TARGET_WIDTH / unscaled.width;
    const viewport = page.getViewport({ scale: scale * dpr });
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      // Never silently skip a page: the user must review the FULL document
      // before signing, so a page that cannot render fails the whole load.
      throw new Error(`could not create a rendering context for page ${pageNo}`);
    }
    await page.render({
      canvasContext: ctx,
      viewport,
      // Draw form-field values (filled by pdf_edit) even when the PDF only
      // carries /NeedAppearances instead of baked appearance streams.
      annotationMode: pdfjsLib.AnnotationMode.ENABLE_FORMS,
    }).promise;
    pages.push({
      pageNo,
      url: canvas.toDataURL("image/png"),
      cssWidth: unscaled.width * scale,
      cssHeight: unscaled.height * scale,
      scale,
      pdfHeight: unscaled.height,
    });
  }
  return pages;
}

export function Signer({
  mcpApp,
  resultBuffer,
}: {
  mcpApp: McpAppLike;
  resultBuffer?: { drainInto: (handler: (raw: unknown) => void) => void };
}) {
  const [phase, setPhase] = useState<Phase>("waiting");
  const [doc, setDoc] = useState<SignerDocument | null>(null);
  const [pages, setPages] = useState<RenderedPage[]>([]);
  const [typedName, setTypedName] = useState("");
  const [consent, setConsent] = useState(false);
  const [outcome, setOutcome] = useState<SignOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadedDocId = useRef<string | null>(null);
  // Monotonic load generation: a newer signing request invalidates any load
  // still awaiting, so stale pages can never render under a newer doc/sign
  // target (the user must only ever sign what they are looking at).
  const loadGeneration = useRef(0);

  const loadDocument = useCallback(
    async (docId: string) => {
      const generation = ++loadGeneration.current;
      const stale = () => generation !== loadGeneration.current;
      setPhase("loading");
      setError(null);
      try {
        const raw = await mcpApp.callServerTool({
          name: "pdf_signer.get_document",
          arguments: { doc_id: docId },
        });
        if (stale()) return;
        const data = extractStructuredContent<SignerDocument>(raw);
        if (data === null) {
          throw new Error("malformed pdf_signer.get_document result");
        }
        const rendered = await renderAllPages(data.data_base64);
        if (stale()) return;
        setDoc(data);
        loadedDocId.current = data.doc_id;
        setPages(rendered);
        if (data.status === "signed") {
          setOutcome({ status: "signed", message: "This document is already signed." });
          setPhase("signed");
        } else {
          setPhase("review");
        }
      } catch (err) {
        if (stale()) return;
        setError(`Could not load the document: ${err}`);
        setPhase("error");
      }
    },
    [mcpApp]
  );

  // The pdf_request_signature tool result carries the doc_id to review. The
  // entry point buffers results delivered before this effect runs (host can
  // race the mount); drainInto replays them and takes over live delivery.
  useEffect(() => {
    const handler = (result: unknown) => {
      const data = extractStructuredContent<{ doc_id?: unknown }>(result);
      const docId = typeof data?.doc_id === "string" ? data.doc_id : null;
      if (docId && docId !== loadedDocId.current) {
        void loadDocument(docId);
      }
    };
    if (resultBuffer) {
      resultBuffer.drainInto(handler);
    } else {
      mcpApp.ontoolresult = handler;
    }
    return () => {
      if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = undefined;
    };
  }, [mcpApp, resultBuffer, loadDocument]);

  const canSign = typedName.trim().length > 0 && consent && phase === "review";

  const handleSign = async () => {
    if (!doc || !canSign) return;
    setPhase("signing");
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "pdf_signer.sign",
        arguments: {
          doc_id: doc.doc_id,
          typed_name: typedName.trim(),
          consent,
        },
      });
      const result = extractStructuredContent<SignOutcome>(raw);
      if (result === null) {
        throw new Error("malformed pdf_signer.sign result");
      }
      setOutcome(result);
      if (result.status === "signed") {
        setPhase("signed");
        try {
          await mcpApp.sendMessage?.({
            role: "user",
            content: [
              {
                type: "text",
                text: `I signed ${doc.filename} as "${result.signed_by}". The document is sealed - please continue.`,
              },
            ],
          });
        } catch {
          // sendMessage is best-effort; the signature itself already succeeded.
        }
      } else {
        // Host confirmation declined - the document is still awaiting.
        setPhase("review");
        setError(result.message ?? "Signing was not confirmed.");
      }
    } catch (err) {
      setPhase("review");
      setError(`Signing failed: ${err}`);
    }
  };

  const handleCancel = async () => {
    if (!doc) return;
    try {
      const raw = await mcpApp.callServerTool({
        name: "pdf_signer.cancel",
        arguments: { doc_id: doc.doc_id },
      });
      // A resolved-but-error tool result (isError) must not show as
      // cancelled: only the server confirming status "open" reopens the doc.
      const result = extractStructuredContent<{ status?: string }>(raw);
      if (result?.status !== "open") {
        throw new Error("the server did not confirm the cancellation");
      }
      setPhase("cancelled");
    } catch (err) {
      setError(`Could not cancel: ${err}`);
    }
  };

  if (phase === "waiting") {
    return <div style={styles.centered}>Waiting for a document to sign…</div>;
  }
  if (phase === "loading") {
    return <div style={styles.centered}>Loading document…</div>;
  }
  if (phase === "error" && !doc) {
    return <div style={{ ...styles.centered, color: "#b3261e" }}>{error}</div>;
  }

  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <span style={styles.filename}>{doc?.filename}</span>
        {phase === "signed" && outcome ? (
          <span style={styles.signedBadge} data-testid="signed-badge">
            ✓ Signed{outcome.signed_by ? ` by ${outcome.signed_by}` : ""}
            {outcome.signed_at_utc ? ` · ${outcome.signed_at_utc}` : ""}
          </span>
        ) : phase === "cancelled" ? (
          <span style={styles.cancelledBadge}>Signing cancelled</span>
        ) : (
          <span style={styles.pendingBadge}>Awaiting your signature</span>
        )}
      </header>

      {phase === "error" && (
        <div style={{ ...styles.errorBanner, margin: "12px 16px 0" }} role="alert">
          {error}
        </div>
      )}
      <div style={styles.pagesScroller} data-testid="pages">
        {pages.map((p) => (
          <div
            key={p.pageNo}
            style={{
              ...styles.pageWrap,
              width: p.cssWidth,
              height: p.cssHeight,
            }}
          >
            <img
              src={p.url}
              alt={`Page ${p.pageNo}`}
              width={p.cssWidth}
              height={p.cssHeight}
              style={{ display: "block" }}
            />
            {phase !== "signed" &&
              doc?.stamp_page === p.pageNo &&
              doc.stamp_rect != null &&
              doc.stamp_rect.length === 4 && (
                <div
                  data-testid="stamp-highlight"
                  style={{
                    ...styles.highlight,
                    left: doc.stamp_rect[0] * p.scale,
                    top: (p.pdfHeight - doc.stamp_rect[3]) * p.scale,
                    width: (doc.stamp_rect[2] - doc.stamp_rect[0]) * p.scale,
                    height: (doc.stamp_rect[3] - doc.stamp_rect[1]) * p.scale,
                  }}
                >
                  <span style={styles.highlightLabel}>Sign here</span>
                </div>
              )}
          </div>
        ))}
      </div>

      {(phase === "review" || phase === "signing") && (
        <footer style={styles.ceremony}>
          {error && (
            <div style={styles.errorBanner} role="alert">
              {error}
            </div>
          )}
          <label style={styles.fieldLabel} htmlFor="sign-as">
            Sign as (full legal name)
          </label>
          <input
            id="sign-as"
            type="text"
            value={typedName}
            onChange={(e) => setTypedName(e.target.value)}
            placeholder="Your full legal name"
            style={styles.nameInput}
            disabled={phase === "signing"}
          />
          <label style={styles.consentRow}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              disabled={phase === "signing"}
            />
            <span>
              I agree that typing my name constitutes my electronic signature.
            </span>
          </label>
          <div style={styles.buttonRow}>
            <button
              onClick={handleCancel}
              style={styles.cancelBtn}
              disabled={phase === "signing"}
            >
              Cancel
            </button>
            <button
              onClick={handleSign}
              style={{
                ...styles.signBtn,
                opacity: canSign ? 1 : 0.5,
                cursor: canSign ? "pointer" : "not-allowed",
              }}
              disabled={!canSign}
            >
              {phase === "signing" ? "Signing…" : "Sign document"}
            </button>
          </div>
        </footer>
      )}
      {phase === "cancelled" && (
        <footer style={styles.ceremony}>
          <div>Signing cancelled - the document can be edited again.</div>
        </footer>
      )}
      {phase === "signed" && outcome?.message && (
        <footer style={styles.ceremony}>
          <div data-testid="signed-message">{outcome.message}</div>
        </footer>
      )}
    </div>
  );
}
