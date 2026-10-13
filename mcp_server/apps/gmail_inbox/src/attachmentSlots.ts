import type { DraftAttachment } from "./types";

/**
 * One file in the composer, in display order.
 *
 * - `confirmed`: on the draft, as the server last reported it. `attachment_id`
 *   is absent for parts Gmail stores inline; such a file is kept by every write
 *   but cannot be removed by id.
 * - `pending`: picked but not yet uploaded. Its bytes go up once, in its own
 *   `add_attachment` call, and the reply turns it into a confirmed slot.
 *
 * `key` is local and stable. `attachment_id` is not: the server rebuilds the
 * draft on every write and Gmail issues fresh ids, so ids are only ever taken
 * from the latest reply.
 */
export type ConfirmedSlot = {
  kind: "confirmed";
  key: number;
  filename: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
};

export type PendingSlot = {
  kind: "pending";
  key: number;
  filename: string;
  mime_type: string;
  size: number;
  data_base64: string;
};

export type AttachmentSlot = ConfirmedSlot | PendingSlot;

/** A slot the next reply is expected to report, in the server's order. */
export type ExpectedFile = { key: number; filename: string };

/**
 * Label a server attachment list with local keys.
 *
 * Each composer write changes the list by a known step (a text save keeps it,
 * an add appends one file, a remove drops one), so the caller passes the list
 * it expects back. When the lengths agree, each position keeps its expected
 * key if the filename matches. Anything else (a concurrent agent edit, a name
 * Gmail re-encoded) gets a fresh key: the file still shows, only a queued
 * remove aimed at the old key will find nothing to remove.
 */
export function labelReply(
  expected: ExpectedFile[],
  reply: DraftAttachment[],
  nextKey: () => number,
): ConfirmedSlot[] {
  const named = reply.filter((a): a is DraftAttachment & { filename: string } => !!a.filename);
  const lined = named.length === expected.length;
  return named.map((a, i) => ({
    kind: "confirmed",
    key: lined && expected[i].filename === a.filename ? expected[i].key : nextKey(),
    filename: a.filename,
    mime_type: a.mime_type,
    size: a.size,
    attachment_id: a.attachment_id,
    message_id: a.message_id,
  }));
}

/** Whether `labelReply` kept every expected key, i.e. the reply is exactly `expected`. */
export function matchesExpected(labelled: ConfirmedSlot[], expected: ExpectedFile[]): boolean {
  return labelled.length === expected.length && labelled.every((s, i) => s.key === expected[i].key);
}
