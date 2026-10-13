// pdf.js ships the worker bundle without type declarations. Signer.tsx only
// hands the module object to globalThis.pdfjsWorker, so its shape is opaque.
declare module "pdfjs-dist/build/pdf.worker.min.mjs";
