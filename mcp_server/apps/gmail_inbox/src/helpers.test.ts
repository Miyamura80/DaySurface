import { describe, expect, it, vi } from "vitest";
import { bufferToolResults, lostReason } from "./helpers";

describe("bufferToolResults", () => {
  it("replays a result delivered before the handler is installed", () => {
    const app: { ontoolresult?: (raw: unknown) => void } = {};
    const buffer = bufferToolResults(app);

    // Host delivers before the component mounts.
    app.ontoolresult?.({ id: "early" });

    const handler = vi.fn();
    buffer.drainInto(handler);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ id: "early" });
  });

  it("replays multiple pre-mount results oldest-first, then delivers live", () => {
    const app: { ontoolresult?: (raw: unknown) => void } = {};
    const buffer = bufferToolResults(app);
    app.ontoolresult?.({ n: 1 });
    app.ontoolresult?.({ n: 2 });

    const seen: unknown[] = [];
    buffer.drainInto((raw) => seen.push(raw));
    // After draining, the live handler is installed and receives new results.
    app.ontoolresult?.({ n: 3 });

    expect(seen).toEqual([{ n: 1 }, { n: 2 }, { n: 3 }]);
  });

  it("no-ops cleanly when nothing was buffered", () => {
    const app: { ontoolresult?: (raw: unknown) => void } = {};
    const buffer = bufferToolResults(app);
    const handler = vi.fn();
    buffer.drainInto(handler);
    expect(handler).not.toHaveBeenCalled();
    app.ontoolresult?.({ id: "later" });
    expect(handler).toHaveBeenCalledWith({ id: "later" });
  });
});

describe("lostReason", () => {
  it("keeps only the title of a host's Cloudflare error body", () => {
    const raw =
      'MCP error -32603: Error POSTing to endpoint: {"type":"https://developers.cloudflare.com/x","title":"Error 502: Bad gateway","status":502,"detail":"The origin web server returned an invalid or incomplete response."}';
    expect(lostReason(new Error(raw))).toBe("Error 502: Bad gateway");
  });

  it("falls back to the status when the body has no title", () => {
    expect(lostReason(new Error('Error POSTing to endpoint: {"status":504}'))).toBe("HTTP 504");
  });

  it("passes short plain text through and truncates long text", () => {
    expect(lostReason(new Error("request timed out"))).toBe("request timed out");
    expect(lostReason(new Error("x".repeat(300)))).toHaveLength(120);
  });
});
