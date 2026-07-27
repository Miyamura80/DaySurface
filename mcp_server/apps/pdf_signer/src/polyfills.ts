// pdfjs-dist 5.x calls Map.prototype.getOrInsertComputed (the ES "upsert"
// proposal), which only very recent Chromiums ship. MCP hosts embed older
// engines (Electron apps lag Chrome by months), so polyfill it here instead
// of requiring a bleeding-edge host. Runs before pdf.js via import order.

/* eslint-disable @typescript-eslint/no-explicit-any */
for (const proto of [Map.prototype, WeakMap.prototype] as any[]) {
  if (typeof proto.getOrInsertComputed !== "function") {
    proto.getOrInsertComputed = function (key: any, compute: (k: any) => any) {
      if (!this.has(key)) this.set(key, compute(key));
      return this.get(key);
    };
  }
  if (typeof proto.getOrInsert !== "function") {
    proto.getOrInsert = function (key: any, defaultValue: any) {
      if (!this.has(key)) this.set(key, defaultValue);
      return this.get(key);
    };
  }
}

export {};
