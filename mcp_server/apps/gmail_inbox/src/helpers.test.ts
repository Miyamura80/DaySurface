import { describe, expect, it, vi } from "vitest";
import { bufferToolResults } from "./helpers";

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
