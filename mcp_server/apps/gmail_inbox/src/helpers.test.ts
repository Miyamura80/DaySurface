import { describe, expect, it, vi } from "vitest";
import { bufferToolResults, buildAttachmentsPayload } from "./helpers";
import type { ExistingAttachment, FileAttachment } from "./types";

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

const upload = (filename: string): FileAttachment => ({
  filename,
  mime_type: "application/pdf",
  data_base64: "QkFTRTY0",
  size: 10,
});

const existing = (attachment_id?: string): ExistingAttachment => ({
  filename: `keep-${attachment_id ?? "none"}.pdf`,
  mime_type: "application/pdf",
  size: 20,
  attachment_id,
  message_id: "m-1",
});

describe("buildAttachmentsPayload", () => {
  it("returns undefined when attachments were not changed so the arg is omitted (preserve-all)", () => {
    expect(buildAttachmentsPayload([], [existing("att-1")], false)).toBeUndefined();
  });

  it("keeps existing files by reference ahead of new uploads", () => {
    const result = buildAttachmentsPayload(
      [upload("new.pdf")],
      [existing("att-1"), existing("att-2")],
      true,
    );
    expect(result).toEqual([
      { attachment_id: "att-1" },
      { attachment_id: "att-2" },
      { filename: "new.pdf", mime_type: "application/pdf", data_base64: "QkFTRTY0" },
    ]);
  });

  it("skips existing attachments that have no attachment_id (cannot be referenced)", () => {
    const result = buildAttachmentsPayload(
      [upload("new.pdf")],
      [existing(undefined), existing("att-9")],
      true,
    );
    expect(result).toEqual([
      { attachment_id: "att-9" },
      { filename: "new.pdf", mime_type: "application/pdf", data_base64: "QkFTRTY0" },
    ]);
  });

  it("emits only the new uploads when there are no existing files", () => {
    const result = buildAttachmentsPayload([upload("a.pdf")], [], true);
    expect(result).toEqual([
      { filename: "a.pdf", mime_type: "application/pdf", data_base64: "QkFTRTY0" },
    ]);
  });

  it("sends existing refs alone when a new upload was added then removed (changed, no uploads)", () => {
    // Removing the last new upload must still emit the desired set so the
    // removal takes effect server-side rather than preserve-all keeping it.
    const result = buildAttachmentsPayload([], [existing("att-1"), existing("att-2")], true);
    expect(result).toEqual([{ attachment_id: "att-1" }, { attachment_id: "att-2" }]);
  });

  it("clears all when everything was removed (changed, nothing left)", () => {
    expect(buildAttachmentsPayload([], [], true)).toEqual([]);
  });
});
