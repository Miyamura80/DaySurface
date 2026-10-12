import { describe, expect, it } from "vitest";
import {
  buildAttachmentWrite,
  reconcileSlots,
  type AttachmentSlot,
} from "./attachmentSlots";

const pick = (key: number, filename: string, size = 10): AttachmentSlot => ({
  key,
  filename,
  mime_type: "application/pdf",
  size,
  data_base64: "QkFTRTY0",
});

const confirmed = (key: number, attachment_id?: string, filename = `f${key}.pdf`): AttachmentSlot => ({
  key,
  filename,
  mime_type: "application/pdf",
  size: 20,
  attachment_id,
  message_id: "m-1",
});

const server = (filename: string, attachment_id: string, size = 20) => ({
  filename,
  mime_type: "application/pdf",
  size,
  attachment_id,
  message_id: "m-2",
});

function counter(start = 100) {
  let n = start;
  return () => ++n;
}

describe("buildAttachmentWrite", () => {
  it("omits the arg when attachments were not changed (preserve-all)", () => {
    const slots = [confirmed(1, "att-1")];
    expect(buildAttachmentWrite(slots, false)).toEqual({ arg: undefined, sent: slots });
  });

  it("references confirmed files and uploads only unconfirmed picks, in order", () => {
    const { arg } = buildAttachmentWrite(
      [confirmed(1, "att-1"), pick(2, "new.pdf"), confirmed(3, "att-3")],
      true,
    );
    expect(arg).toEqual([
      { attachment_id: "att-1" },
      { filename: "new.pdf", mime_type: "application/pdf", data_base64: "QkFTRTY0" },
      { attachment_id: "att-3" },
    ]);
  });

  it("drops a confirmed file with no attachment_id (it cannot be referenced)", () => {
    const { arg, sent } = buildAttachmentWrite([confirmed(1), confirmed(2, "att-2")], true);
    expect(arg).toEqual([{ attachment_id: "att-2" }]);
    expect(sent.map((s) => s.key)).toEqual([2]);
  });

  it("clears all when everything was removed", () => {
    expect(buildAttachmentWrite([], true).arg).toEqual([]);
  });
});

describe("reconcileSlots", () => {
  it("turns a confirmed pick into a reference so it is never uploaded again", () => {
    const sent = [confirmed(1, "att-1", "a.pdf"), pick(2, "b.pdf")];
    const next = reconcileSlots(
      sent,
      sent,
      [server("a.pdf", "att-9"), server("b.pdf", "att-10", 10)],
      counter(),
    );
    expect(next.map((s) => [s.key, s.attachment_id, s.data_base64])).toEqual([
      [1, "att-9", undefined],
      [2, "att-10", undefined],
    ]);
    expect(buildAttachmentWrite(next, true).arg).toEqual([
      { attachment_id: "att-9" },
      { attachment_id: "att-10" },
    ]);
  });

  it("leaves a pick made while the write was in flight pending", () => {
    const sent = [confirmed(1, "att-1", "a.pdf")];
    const current = [...sent, pick(2, "late.pdf")];
    const next = reconcileSlots(current, sent, [server("a.pdf", "att-9")], counter());
    expect(next[0].attachment_id).toBe("att-9");
    expect(next[1]).toEqual(pick(2, "late.pdf"));
  });

  it("does not resurrect a file removed while the write was in flight", () => {
    const sent = [confirmed(1, "att-1", "a.pdf"), confirmed(2, "att-2", "b.pdf")];
    const current = [sent[1]];
    const next = reconcileSlots(
      current,
      sent,
      [server("a.pdf", "att-8"), server("b.pdf", "att-9")],
      counter(),
    );
    expect(next.map((s) => [s.key, s.attachment_id])).toEqual([[2, "att-9"]]);
  });

  it("takes the server's list when the reply does not line up, keeping unmatched picks", () => {
    const sent = [pick(1, "a.pdf"), pick(2, "b.pdf")];
    const current = [...sent, pick(3, "c.pdf")];
    // The send's update never landed: the server still has only x.pdf.
    const next = reconcileSlots(current, sent, [server("x.pdf", "att-x")], counter());
    expect(next.map((s) => [s.filename, s.attachment_id ?? "pending"])).toEqual([
      ["x.pdf", "att-x"],
      ["a.pdf", "pending"],
      ["b.pdf", "pending"],
      ["c.pdf", "pending"],
    ]);
  });

  it("drops a pick the mismatched server list already holds, once per copy", () => {
    const sent = [pick(1, "dup.pdf"), pick(2, "dup.pdf")];
    const next = reconcileSlots(sent, sent, [server("dup.pdf", "att-1", 10)], counter());
    expect(next.map((s) => s.attachment_id ?? "pending")).toEqual(["att-1", "pending"]);
  });
});
