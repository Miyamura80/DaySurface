import "@testing-library/jest-dom/vitest";

// jsdom (used so DOMPurify can sanitize email HTML) lacks the canvas APIs that
// pdfjs-dist touches at import time. The inbox tests never rasterize a real
// PDF, so minimal stubs are enough to let the module load.
type Ctor = new (...args: unknown[]) => object;
const g = globalThis as unknown as Record<string, Ctor>;
for (const name of ["DOMMatrix", "Path2D", "ImageData"]) {
  if (typeof g[name] === "undefined") {
    g[name] = class {};
  }
}
