import { useEffect, useRef, useState } from "react";
import type { ComposerDraft, ComposerSaveStatus, DraftAttachment, McpAppLike } from "./types";
import { callWriteTool, confirmSend, draftFieldsEqual, errMsg, extractStructuredContent } from "./helpers";
import { reportComposerAction } from "./modelContext";
import {
  labelReply,
  matchesExpected,
  type AttachmentSlot,
  type ConfirmedSlot,
  type ExpectedFile,
  type PendingSlot,
} from "./attachmentSlots";

/** A picked file, read and ready to upload. */
export type PickedFile = Omit<PendingSlot, "kind" | "key">;

const expectedOf = (slots: ConfirmedSlot[]): ExpectedFile[] =>
  slots.map(({ key, filename }) => ({ key, filename }));

const isConfirmed = (s: AttachmentSlot): s is ConfirmedSlot => s.kind === "confirmed";

/**
 * Every write the composer makes to its draft, on one queue.
 *
 * Text autosave and send carry fields only, so they never cite an attachment
 * id. Files change one at a time: a pick is its own `add_attachment` call (its
 * bytes go up once), a removal its own `remove_attachment` call, and each reply
 * is the draft's new file list. Because writes never overlap, each one knows
 * exactly how the list should change, which is how local keys survive Gmail
 * re-issuing every id on every write. Discard is the exception: it does not
 * wait for the queue, and the latches below silence whatever is still on it.
 */
export function useDraftWriter({
  draft,
  mcpApp,
  onDraftChange,
  onDiscard,
  onSent,
}: {
  draft: ComposerDraft;
  mcpApp: McpAppLike;
  onDraftChange: (d: ComposerDraft) => void;
  onDiscard: () => void;
  onSent: () => void;
}) {
  const draftId = draft.draft_id;
  const [saveStatus, setSaveStatus] = useState<ComposerSaveStatus>({ kind: "idle" });
  const keyRef = useRef(0);
  const nextKey = () => ++keyRef.current;
  const [slots, setSlotsState] = useState<AttachmentSlot[]>(
    () => labelReply([], draft.attachments ?? [], nextKey),
  );
  // Mirrors `slots` synchronously: a queued write runs after renders it did not
  // see, and must build on the latest list rather than a stale closure.
  const slotsRef = useRef(slots);
  const setSlots = (next: AttachmentSlot[]) => {
    slotsRef.current = next;
    setSlotsState(next);
  };
  const confirmed = () => slotsRef.current.filter(isConfirmed);
  /** Replace the confirmed files, keeping picks still waiting (minus `uploaded`). */
  const setConfirmed = (next: ConfirmedSlot[], uploaded?: number) =>
    setSlots([...next, ...slotsRef.current.filter((s) => s.kind === "pending" && s.key !== uploaded)]);
  // Files the user removed whose remove is still queued: hidden, still counted.
  const [removing, setRemoving] = useState<ReadonlySet<number>>(new Set());

  // The fields the next write should carry: set synchronously on every edit, so
  // a queued write sends what the user typed even before the prop round-trips.
  const fieldsRef = useRef(draft);
  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set when send or discard commits, so queued text saves stop and no late
  // reply overwrites the terminal state. Released if the send fails.
  const closingRef = useRef(false);
  // Set once the draft is gone (sent or discarded). File edits queued before a
  // send still run, since the send waits behind them; after this they cannot.
  const doneRef = useRef(false);
  // Bumped when a terminal action commits: a text save only reports if its
  // generation is still current, so one started before a failed send cannot
  // write "saved" over the send's error.
  const writeGenRef = useRef(0);
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = <T,>(task: () => Promise<T>): Promise<T> => {
    const run = chainRef.current.then(task);
    chainRef.current = run.catch(() => {});
    return run;
  };
  const report = (s: ComposerSaveStatus) => { if (!closingRef.current) setSaveStatus(s); };

  useEffect(() => { fieldsRef.current = draft; }, [draft]);

  // A refresh or agent edit brings a new file list. It goes through the queue
  // so it cannot interleave with a write; if it is stale, the next remove
  // fails, re-reads the draft and retries.
  const syncedRef = useRef(draft.attachments);
  useEffect(() => {
    const incoming = draft.attachments;
    if (!incoming || incoming === syncedRef.current) return;
    syncedRef.current = incoming;
    void enqueue(async () => setConfirmed(labelReply(expectedOf(confirmed()), incoming, nextKey)));
  }, [draft.attachments]);

  const fieldArgs = (d: ComposerDraft) => ({
    draft_id: draftId,
    to: d.to ?? "",
    cc: d.cc ?? "",
    bcc: d.bcc ?? "",
    subject: d.subject ?? "",
    body: d.body ?? "",
  });

  const replyFiles = (raw: unknown) =>
    extractStructuredContent<{ attachments?: DraftAttachment[] }>(raw)?.attachments;

  // Re-read the draft after a file write that failed: an error reply can
  // follow a write that landed, and a lost reply says nothing. Returns the list
  // labelled by the first candidate it matches exactly, else fresh keys.
  const recover = async (candidates: ExpectedFile[][]): Promise<ConfirmedSlot[] | null> => {
    try {
      const raw = await callWriteTool(mcpApp, "gmail_composer.refresh", { draft_id: draftId });
      const files = replyFiles(raw) ?? [];
      for (const c of candidates) {
        const labelled = labelReply(c, files, nextKey);
        if (matchesExpected(labelled, c)) return labelled;
      }
      return labelReply([], files, nextKey);
    } catch {
      return null;
    }
  };

  const persistDraft = () => enqueue(async () => {
    if (closingRef.current) return;
    const gen = writeGenRef.current;
    const stale = () => closingRef.current || gen !== writeGenRef.current;
    const snapshot = fieldsRef.current;
    const expected = expectedOf(confirmed());
    setSaveStatus({ kind: "saving" });
    try {
      const raw = await callWriteTool(mcpApp, "gmail_composer.save_draft", fieldArgs(snapshot));
      const files = replyFiles(raw);
      if (files) setConfirmed(labelReply(expected, files, nextKey));
      if (stale()) return;
      setSaveStatus({ kind: "saved", at: new Date() });
      if (draftFieldsEqual(fieldsRef.current, snapshot)) localDirtyRef.current = false;
    } catch (err) {
      if (stale()) return;
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  });

  const scheduleAutoSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => void persistDraft(), 800);
  };

  const updateField = (key: keyof ComposerDraft, value: string) => {
    const next = { ...fieldsRef.current, [key]: value };
    fieldsRef.current = next;
    onDraftChange(next);
    localDirtyRef.current = true;
    scheduleAutoSave();
  };

  /** Take an agent's version of the draft over the user's unsaved edits. */
  const applyDraft = (d: ComposerDraft) => {
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    fieldsRef.current = d;
    onDraftChange(d);
    localDirtyRef.current = false;
  };

  /** Upload one pick. Resolves to an error message if it did not land. */
  const addFile = (file: PickedFile): Promise<string | null> => {
    const slot: PendingSlot = { kind: "pending", key: nextKey(), ...file };
    setSlots([...slotsRef.current, slot]);
    return enqueue(async () => {
      // Removed before its turn: nothing was uploaded, so nothing to undo.
      if (doneRef.current || !slotsRef.current.some((s) => s.key === slot.key)) return null;
      const before = expectedOf(confirmed());
      const after = [...before, { key: slot.key, filename: slot.filename }];
      report({ kind: "saving" });
      try {
        const raw = await callWriteTool(mcpApp, "gmail_composer.add_attachment", {
          draft_id: draftId,
          filename: slot.filename,
          mime_type: slot.mime_type,
          data_base64: slot.data_base64,
        });
        setConfirmed(labelReply(after, replyFiles(raw) ?? [], nextKey), slot.key);
        report({ kind: "saved", at: new Date() });
        return null;
      } catch (err) {
        const healed = await recover([after, before]);
        if (healed) setConfirmed(healed, slot.key);
        else setSlots(slotsRef.current.filter((s) => s.key !== slot.key));
        const landed = !!healed?.some((s) => s.key === slot.key);
        report(landed ? { kind: "saved", at: new Date() } : { kind: "idle" });
        return landed ? null : errMsg(err);
      }
    });
  };

  const removeFile = (key: number) => {
    setRemoving((cur) => new Set(cur).add(key));
    // A pick that has not started uploading just disappears: its queued add
    // sees it gone and skips. One already uploading is removed by the queued
    // remove below, once its add has confirmed it.
    setSlots(slotsRef.current.filter((s) => !(s.kind === "pending" && s.key === key)));
    return enqueue(async () => {
      try {
        // Two tries: a refresh or agent edit can leave a stale id, which the
        // first failure's re-read replaces.
        for (let attempt = 0; attempt < 2; attempt++) {
          const target = confirmed().find((s) => s.key === key);
          if (doneRef.current || !target?.attachment_id) return;
          const before = expectedOf(confirmed());
          const after = before.filter((e) => e.key !== key);
          report({ kind: "saving" });
          try {
            const raw = await callWriteTool(mcpApp, "gmail_composer.remove_attachment", {
              draft_id: draftId,
              attachment_id: target.attachment_id,
            });
            setConfirmed(labelReply(after, replyFiles(raw) ?? [], nextKey));
            report({ kind: "saved", at: new Date() });
            return;
          } catch (err) {
            const healed = await recover([after, before]);
            if (healed) setConfirmed(healed);
            const still = healed?.find((s) => s.key === key);
            if (!still) { report({ kind: "saved", at: new Date() }); return; }
            if (still.attachment_id !== target.attachment_id) continue;
            report({ kind: "error", message: `Couldn't remove ${target.filename}: ${errMsg(err)}` });
            return;
          }
        }
      } finally {
        setRemoving((cur) => { const next = new Set(cur); next.delete(key); return next; });
      }
    });
  };

  const send = async () => {
    // Single-flight: a repeat click while a send is in flight is a no-op.
    if (closingRef.current) return;
    closingRef.current = true;
    writeGenRef.current++;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus({ kind: "sending" });
    const fields = fieldsRef.current;
    try {
      // Queued behind file edits made before the click, which it includes.
      const sent = await enqueue(() => confirmSend(mcpApp, fieldArgs(fields)));
      doneRef.current = true;
      setSaveStatus({ kind: "sent", message_id: sent.message_id });
      void reportComposerAction(mcpApp, "sent", fields, sent);
      setTimeout(onSent, 1500);
    } catch (err) {
      // The send did not land, so the composer stays editable: reopen it to
      // autosaves and let the user retry.
      closingRef.current = false;
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const discard = async () => {
    if (closingRef.current) return;
    closingRef.current = true;
    doneRef.current = true;
    writeGenRef.current++;
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDiscard();
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draftId },
      });
      // Like send, a tool-level failure still resolves: only a confirmed
      // discard may tell the model the draft is gone.
      if (extractStructuredContent<{ discarded?: boolean }>(raw)?.discarded) {
        void reportComposerAction(mcpApp, "discarded", fieldsRef.current);
      }
    } catch { /* discard is best-effort */ }
  };

  return {
    saveStatus,
    slots: slots.filter((s) => !removing.has(s.key)),
    slotsRef,
    fieldsRef,
    localDirtyRef,
    updateField,
    applyDraft,
    addFile,
    removeFile,
    send,
    discard,
  };
}
