import { describe, expect, it } from "vitest";
import { labelReply, matchesExpected } from "./attachmentSlots";

const server = (filename: string, attachment_id?: string) => ({
  filename,
  mime_type: "application/pdf",
  size: 20,
  attachment_id,
  message_id: "m-2",
});

function counter(start = 100) {
  let n = start;
  return () => ++n;
}

describe("labelReply", () => {
  it("keeps expected keys and takes the reply's fresh ids", () => {
    const expected = [{ key: 1, filename: "a.pdf" }, { key: 2, filename: "b.pdf" }];
    const labelled = labelReply(expected, [server("a.pdf", "att-8"), server("b.pdf", "att-9")], counter());
    expect(labelled.map((s) => [s.key, s.attachment_id])).toEqual([[1, "att-8"], [2, "att-9"]]);
    expect(matchesExpected(labelled, expected)).toBe(true);
  });

  it("gives a renamed file a fresh key but keeps its neighbours", () => {
    const expected = [{ key: 1, filename: "a.pdf" }, { key: 2, filename: "résumé.pdf" }];
    const labelled = labelReply(expected, [server("a.pdf", "x"), server("résumé.pdf", "y")], counter());
    expect(labelled.map((s) => s.key)).toEqual([1, 101]);
    expect(matchesExpected(labelled, expected)).toBe(false);
  });

  it("gives every file a fresh key when the count is not what was expected", () => {
    const expected = [{ key: 1, filename: "a.pdf" }];
    const labelled = labelReply(expected, [server("a.pdf", "x"), server("b.pdf", "y")], counter());
    expect(labelled.map((s) => s.key)).toEqual([101, 102]);
  });

  it("keeps a file Gmail stores without an id, as confirmed", () => {
    const labelled = labelReply([{ key: 1, filename: "inline.png" }], [server("inline.png")], counter());
    expect(labelled).toEqual([expect.objectContaining({ kind: "confirmed", key: 1, attachment_id: undefined })]);
  });

  it("skips unnamed parts", () => {
    const labelled = labelReply([], [{ attachment_id: "x" }], counter());
    expect(labelled).toEqual([]);
  });
});
