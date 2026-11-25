import { describe, expect, it, vi } from "vitest";
import { bufferToolResults, errMsg } from "./helpers";

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

describe("errMsg", () => {
  const cloudflare502 =
    'MCP error -32603: Error POSTing to endpoint: {"type":"https://developers.cloudflare.com/x","title":"Error 502: Bad gateway","status":502,"detail":"The origin web server returned an invalid or incomplete response."}';

  it("keeps only the title of a host's Cloudflare error body", () => {
    expect(errMsg(new Error(cloudflare502))).toBe("Error 502: Bad gateway");
  });

  it("finds the body even with text after it", () => {
    expect(errMsg(new Error(`${cloudflare502} (retry later)`))).toBe("Error 502: Bad gateway");
  });

  it("falls back to the status when the body has no title", () => {
    expect(errMsg(new Error('Error POSTing to endpoint: {"status":504}'))).toBe("HTTP 504");
  });

  it("cuts an unparseable body short", () => {
    const text = errMsg(new Error(`Error POSTing to endpoint: {${"x".repeat(300)}}`));
    expect(text).toHaveLength(120);
    expect(text.endsWith("...")).toBe(true);
  });

  it("passes ordinary messages through, however long", () => {
    const long = "This draft is no longer in Gmail. ".repeat(6);
    expect(errMsg(new Error(long))).toBe(long);
    expect(errMsg("request timed out")).toBe("request timed out");
  });
});
