import type { McpAppLike } from "./Signer";

export type ToolResultBuffer = {
  /**
   * Take over live `ontoolresult` delivery with `handler`, first replaying
   * (oldest first) any results the host delivered before this call.
   */
  drainInto: (handler: (raw: unknown) => void) => void;
};

/**
 * Capture `ontoolresult` deliveries from the earliest possible moment.
 *
 * The host can deliver pdf_request_signature's result before the Signer's
 * mount effect assigns its `ontoolresult` handler; that result would
 * otherwise be lost and the app would sit on "Waiting for a document" with no
 * doc_id to load. Installing this buffer in the entry point (before
 * `connect()`/render) captures the result no matter when it arrives; the
 * component calls `drainInto(handler)` on mount to replay and take over.
 *
 * Replay-then-assign is gapless: there is no `await` between the two, so no
 * postMessage-driven `ontoolresult` can interleave and be dropped. Same
 * pattern as gmail_inbox - local copy pending the shared per-app helpers
 * consolidation (issue #170).
 */
export function bufferToolResults(
  app: Pick<McpAppLike, "ontoolresult">,
): ToolResultBuffer {
  const pending: unknown[] = [];
  app.ontoolresult = (raw) => {
    pending.push(raw);
  };
  return {
    drainInto(handler) {
      const buffered = pending.splice(0, pending.length);
      for (const raw of buffered) handler(raw);
      app.ontoolresult = handler;
    },
  };
}
