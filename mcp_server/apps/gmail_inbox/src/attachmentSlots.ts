import type { DraftAttachment } from "./types";

/**
 * One file in the composer, in display order. A slot with `data_base64` is a
 * pick the server has not confirmed yet; once a save's response echoes it back
 * the bytes are dropped and the slot carries the server's `attachment_id`.
 *
 * `key` is local and stable. `attachment_id` is not: the server rebuilds the
 * whole draft on every update and Gmail hands every file a fresh id, so a ref
 * is only valid until the next write lands. Ids are advanced only from server
 * responses (never by optimistic UI), which is why writes are serialized.
 */
export type AttachmentSlot = {
  key: number;
  filename: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
  data_base64?: string;
};

export type AttachmentWrite = {
  /** The `attachments` argument, or undefined to omit it (server keeps all). */
  arg: Array<Record<string, unknown>> | undefined;
  /** The slots this write puts on the draft, in the order the server stores them. */
  sent: AttachmentSlot[];
};

export function isPending(s: AttachmentSlot): boolean {
  return s.data_base64 !== undefined;
}

export function slotsFromDraft(
  attachments: DraftAttachment[],
  nextKey: () => number,
): AttachmentSlot[] {
  return attachments
    .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
    .map((a) => ({
      key: nextKey(),
      filename: a.filename,
      mime_type: a.mime_type,
      size: a.size,
      attachment_id: a.attachment_id,
      message_id: a.message_id,
    }));
}

// The backend treats an omitted `attachments` as "preserve every file" and a
// list as the whole desired set (refs keep a file, uploads add one).
//   - unchanged -> omit. The common text-only path, and the only way to keep a
//     file that carries no referenceable id.
//   - changed   -> the explicit set, so a removal sticks. Confirmed files go by
//     `{attachment_id}`; only unconfirmed picks carry bytes, so an upload is
//     sent once rather than on every save.
export function buildAttachmentWrite(slots: AttachmentSlot[], changed: boolean): AttachmentWrite {
  if (!changed) return { arg: undefined, sent: slots };
  const sent = slots.filter((s) => s.attachment_id || isPending(s));
  const arg = sent.map((s) =>
    isPending(s)
      ? { filename: s.filename, mime_type: s.mime_type, data_base64: s.data_base64 }
      : { attachment_id: s.attachment_id },
  );
  return { arg, sent };
}

/**
 * Fold a write's server-confirmed attachment list back into the current slots.
 *
 * The server stores files in request order, so when the reply lines up with
 * `sent` (same length, same filenames) each sent slot takes its new id and
 * drops its bytes. Slots added since the request went out stay pending, and
 * slots removed since then stay removed - the next write drops them.
 *
 * If the reply does not line up, the server's list wins outright, and only
 * picks it does not account for are kept as pending.
 */
export function reconcileSlots(
  current: AttachmentSlot[],
  sent: AttachmentSlot[],
  server: DraftAttachment[],
  nextKey: () => number,
): AttachmentSlot[] {
  const confirmed = server.filter((a) => a.filename);
  const lined =
    confirmed.length === sent.length &&
    confirmed.every((a, i) => a.filename === sent[i].filename);
  if (lined) {
    const byKey = new Map(sent.map((s, i) => [s.key, confirmed[i]]));
    return current.map((s) => {
      const a = byKey.get(s.key);
      if (!a) return s;
      return {
        key: s.key,
        filename: s.filename,
        mime_type: a.mime_type ?? s.mime_type,
        size: a.size ?? s.size,
        attachment_id: a.attachment_id,
        message_id: a.message_id,
      };
    });
  }
  // A pick the server already holds is matched once by filename+size; a budget
  // (not a set) so several distinct files sharing both are not all cancelled.
  const budget = new Map<string, number>();
  for (const a of confirmed) {
    const k = `${a.filename}:${a.size}`;
    budget.set(k, (budget.get(k) ?? 0) + 1);
  }
  const sentKeys = new Set(sent.map((s) => s.key));
  const pending = current.filter((s) => {
    if (!isPending(s)) return false;
    if (!sentKeys.has(s.key)) return true;
    const k = `${s.filename}:${s.size}`;
    const n = budget.get(k) ?? 0;
    if (n === 0) return true;
    budget.set(k, n - 1);
    return false;
  });
  return [...slotsFromDraft(confirmed, nextKey), ...pending];
}
