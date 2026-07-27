import { useCallback, useEffect, useRef, useState } from "react";
import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  pdfNavBtn,
  previewBodyStyle,
  previewCloseBtn,
  previewHeaderStyle,
  previewModalStyle,
  previewOverlayStyle,
} from "./messageStyles";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;

export type PreviewData = { url: string; filename: string; mime_type: string };

export function PdfViewer({ url }: { url: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        if (cancelled) return;
        return pdfjsLib.getDocument({ data: new Uint8Array(buf), isEvalSupported: false }).promise;
      })
      .then((doc) => {
        if (cancelled || !doc) return;
        setPdfDoc(doc);
        setNumPages(doc.numPages);
        setPage(1);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load PDF");
      });
    return () => { cancelled = true; };
  }, [url]);

  const renderPage = useCallback(async (doc: pdfjsLib.PDFDocumentProxy, pageNum: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pg = await doc.getPage(pageNum);
    const container = canvas.parentElement;
    const containerWidth = container ? container.clientWidth - 16 : 600;
    const unscaled = pg.getViewport({ scale: 1 });
    const scale = containerWidth / unscaled.width;
    const viewport = pg.getViewport({ scale });
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    await pg.render({ canvasContext: ctx, viewport }).promise;
  }, []);

  useEffect(() => {
    if (pdfDoc && page >= 1 && page <= numPages) {
      renderPage(pdfDoc, page);
    }
  }, [pdfDoc, page, numPages, renderPage]);

  if (error) return <div style={{ padding: 32, textAlign: "center", color: "#d93025" }}>{error}</div>;
  if (!pdfDoc) return <div style={{ padding: 32, textAlign: "center", color: "#5f6368" }}>Loading PDF…</div>;

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
      {numPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, padding: "6px 0", borderBottom: "1px solid #e0e0e0", flexShrink: 0 }}>
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} style={pdfNavBtn} title="Previous page">
            <CaretLeft size={16} />
          </button>
          <span style={{ fontSize: 13, color: "#444" }}>{page} / {numPages}</span>
          <button onClick={() => setPage((p) => Math.min(numPages, p + 1))} disabled={page >= numPages} style={pdfNavBtn} title="Next page">
            <CaretRight size={16} />
          </button>
        </div>
      )}
      <div style={{ flex: 1, overflow: "auto", display: "flex", justifyContent: "center", padding: 8 }}>
        <canvas ref={canvasRef} style={{ maxWidth: "100%" }} />
      </div>
    </div>
  );
}

// Shared attachment-preview modal, used identically by the reader and the
// inline composer. Renders a PDF, an image, or an "unsupported" placeholder.
export function PreviewModal({ preview, onClose }: { preview: PreviewData; onClose: () => void }) {
  return (
    <div style={previewOverlayStyle} onClick={onClose}>
      <div style={previewModalStyle} onClick={(e) => e.stopPropagation()}>
        <div style={previewHeaderStyle}>
          <span style={{ fontSize: 14, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {preview.filename}
          </span>
          <button onClick={onClose} style={previewCloseBtn}>×</button>
        </div>
        <div style={previewBodyStyle}>
          {preview.mime_type === "application/pdf" ? (
            <PdfViewer url={preview.url} />
          ) : preview.mime_type.startsWith("image/") ? (
            <img
              src={preview.url}
              alt={preview.filename}
              style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
            />
          ) : (
            <div style={{ padding: 32, textAlign: "center", color: "#5f6368" }}>
              Preview not available for this file type.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
