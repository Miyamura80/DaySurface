import { useRef, useState } from "react";
import type { Draft, DraftAttachment, SaveStatus } from "./types";
import { secondaryButtonStyle } from "./styles";

// Cap a single attachment just under the server's limit: AttachmentInput caps
// base64 at 34M chars (~25.5 MB decoded), and Gmail's ceiling is 25 MB for the
// WHOLE message. 25 MB (decimal) keeps a per-file drop safely inside the base64
// validator so the client guard actually prevents a wasted round-trip. The
// total-message limit (many files summing past 25 MB) is still enforced
// server-side and surfaces as a save/send error.
export const MAX_ATTACHMENT_BYTES = 25_000_000;

// A ready-to-upload attachment: the server's AttachmentInput shape.
export type NewUpload = { filename: string; mime_type: string; data_base64: string };

// An attachment saved into the whole-set replace: a reference to an existing
// file (kept by id) or a fresh upload (bytes).
export type SaveAttachment = { attachment_id: string } | NewUpload;

// A file the user just dropped/selected, tracked while it is read + uploaded.
// Persisted attachments (with a real attachment_id) live on `draft.attachments`;
// these transient entries disappear once the save response echoes them back, or
// stick around with an error the user can dismiss.
export type PendingUpload = {
  local_id: string;
  filename: string;
  mime_type: string;
  size: number;
  status: "reading" | "uploading" | "error";
  error?: string;
};

// Read a File to bare base64 (no data: URL prefix), the shape AttachmentInput
// wants. FileReader is available in every host iframe and in jsdom.
export function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

// Decimal (SI) units, matching how the 25 MB limit is expressed to the user so
// a file shown as "25.0 MB" is exactly at the cap, not off by the 1024 factor.
export function formatBytes(n: number | undefined): string {
  if (typeof n !== "number" || n <= 0) return "";
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${Math.round(n / 1000)} KB`;
  return `${(n / 1_000_000).toFixed(1)} MB`;
}

type UseAttachmentsArgs = {
  draftRef: React.RefObject<Draft | null>;
  setDraft: React.Dispatch<React.SetStateAction<Draft | null>>;
  setSaveStatus: (s: SaveStatus) => void;
  // The last server-confirmed attachment set - the authoritative keep-list a
  // whole-set replace must echo back. Advanced only by server responses.
  attachmentsRef: React.RefObject<DraftAttachment[]>;
  // The composer's serialized save primitive: performs a save_draft with the
  // given full attachment set (undefined = omit, preserving files) and returns
  // the saved draft. Serialization guarantees prior ops committed first.
  doSave: (attachments?: SaveAttachment[]) => Promise<Draft | null>;
  enqueue: <T>(fn: () => Promise<T>) => Promise<T>;
  // Set once a send/discard is committed: attachment mutations must not touch
  // the terminal draft after that point.
  closingRef: React.RefObject<boolean>;
};

// All attachment UI state + file handling. Kept out of Composer so the
// component stays small; the save serialization lives in Composer (doSave +
// enqueue) because it also governs text autosave and send.
export function useAttachments({
  draftRef,
  setDraft,
  setSaveStatus,
  attachmentsRef,
  doSave,
  enqueue,
  closingRef,
}: UseAttachmentsArgs) {
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const uploadSeqRef = useRef(0);
  const nextUploadId = () => `u${(uploadSeqRef.current += 1)}`;

  const onFilesChosen = async (files: FileList | File[] | null) => {
    if (!draftRef.current || !files) return;
    const chosen = Array.from(files);
    if (chosen.length === 0) return;

    // Classify up front. Empty (0-byte) and oversized files never leave the
    // browser: an empty file would send an empty data_base64 the server
    // rejects, so it's an inline error, not a save attempt.
    const okFiles = chosen.filter((f) => f.size > 0 && f.size <= MAX_ATTACHMENT_BYTES);
    const okEntries: PendingUpload[] = okFiles.map((f) => ({
      local_id: nextUploadId(),
      filename: f.name,
      mime_type: f.type || "application/octet-stream",
      size: f.size,
      status: "reading",
    }));
    const invalidEntries: PendingUpload[] = chosen
      .filter((f) => f.size === 0 || f.size > MAX_ATTACHMENT_BYTES)
      .map((f) => ({
        local_id: nextUploadId(),
        filename: f.name,
        mime_type: f.type || "application/octet-stream",
        size: f.size,
        status: "error",
        error:
          f.size === 0
            ? "Empty file (0 bytes)"
            : `Too large (${formatBytes(f.size)}); ${formatBytes(MAX_ATTACHMENT_BYTES)} max`,
      }));
    setUploads((u) => [...u, ...okEntries, ...invalidEntries]);
    if (okFiles.length === 0) return;

    const okIds = okEntries.map((e) => e.local_id);
    const markOkError = (msg: string) =>
      setUploads((u) =>
        u.map((e) =>
          okIds.includes(e.local_id) ? { ...e, status: "error", error: msg } : e,
        ),
      );

    // Once a send/discard is committed the draft is terminal - don't start a
    // new attachment op against it.
    if (closingRef.current) {
      markOkError("Draft was closed");
      return;
    }

    // Register the whole read + save as ONE serialized unit, synchronously,
    // BEFORE awaiting the read. A Send/Discard clicked mid-read is enqueued
    // AFTER this, so the message can't go out missing a just-dropped file.
    try {
      const result = await enqueue(async () => {
        // Cumulative preflight against the committed set: refuse a batch that
        // would blow past Gmail's total-message limit before reading hundreds
        // of MB of base64 into the iframe. Fail CLOSED on an unknown existing
        // size - existing files are server-side references (bytes never re-read
        // here) whose sizes the server virtually always provides; coalescing a
        // missing one to 0 would undercount and let an over-limit batch through,
        // so treat unknown as "can't fit" rather than guessing low.
        const existingBytes = attachmentsRef.current.reduce(
          (s, a) => s + (a.size ?? MAX_ATTACHMENT_BYTES),
          0,
        );
        const incomingBytes = okFiles.reduce((s, f) => s + f.size, 0);
        if (existingBytes + incomingBytes > MAX_ATTACHMENT_BYTES) {
          return { kind: "too_big" as const };
        }
        // Read each file independently: one unreadable file must not sink the
        // whole batch, so failures are marked per-chip and the rest upload.
        const settled = await Promise.allSettled(okFiles.map(readFileAsBase64));
        const ready: { id: string; upload: NewUpload }[] = [];
        const failedIds: string[] = [];
        settled.forEach((r, i) => {
          const e = okEntries[i];
          if (r.status === "fulfilled" && r.value) {
            ready.push({
              id: e.local_id,
              upload: {
                filename: e.filename,
                mime_type: e.mime_type,
                data_base64: r.value,
              },
            });
          } else {
            failedIds.push(e.local_id);
          }
        });
        if (failedIds.length > 0) {
          setUploads((u) =>
            u.map((e) =>
              failedIds.includes(e.local_id)
                ? { ...e, status: "error", error: "Could not read file" }
                : e,
            ),
          );
        }
        if (ready.length === 0) return { kind: "none" as const };
        const readySet = new Set(ready.map((r) => r.id));
        setUploads((u) =>
          u.map((e) =>
            readySet.has(e.local_id) ? { ...e, status: "uploading" } : e,
          ),
        );
        // Keep-set from the authoritative server-confirmed list at run time, so
        // overlapping drops can't drop one another's files.
        const keep: SaveAttachment[] = attachmentsRef.current.map((a) => ({
          attachment_id: a.attachment_id,
        }));
        const saved = await doSave([...keep, ...ready.map((r) => r.upload)]);
        return { kind: "saved" as const, saved, readyIds: ready.map((r) => r.id) };
      });

      if (result.kind === "too_big") {
        markOkError(`Would exceed ${formatBytes(MAX_ATTACHMENT_BYTES)} total`);
        return;
      }
      if (result.kind === "none") {
        setSaveStatus({ kind: "error", message: "Could not read the selected file(s)" });
        return;
      }
      // Commit the adopted list and drop the transient chips in one batch, so
      // the just-uploaded file never renders as both a chip and a saved row.
      const next = result.saved?.attachments ?? attachmentsRef.current;
      const readySet = new Set(result.readyIds);
      setDraft((d) => (d ? { ...d, attachments: next } : d));
      setUploads((u) => u.filter((e) => !readySet.has(e.local_id)));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      markOkError(msg);
    }
  };

  const onRemoveAttachment = async (attachmentId: string) => {
    // Don't mutate a draft that is being sent/discarded.
    if (!draftRef.current || closingRef.current) return;
    // Optimistic UI only. The authoritative keep-set is recomputed inside the
    // queued op from attachmentsRef (advanced by server responses, not by this
    // optimistic edit), so a remove that overlaps an upload can't clobber it.
    setDraft((d) =>
      d
        ? {
            ...d,
            attachments: (d.attachments ?? []).filter(
              (a) => a.attachment_id !== attachmentId,
            ),
          }
        : d,
    );
    try {
      const saved = await enqueue(() => {
        const keep: SaveAttachment[] = attachmentsRef.current
          .filter((a) => a.attachment_id !== attachmentId)
          .map((a) => ({ attachment_id: a.attachment_id }));
        return doSave(keep);
      });
      const next = saved?.attachments ?? attachmentsRef.current;
      setDraft((d) => (d ? { ...d, attachments: next } : d));
    } catch {
      // doSave only advances attachmentsRef on success, so it still holds the
      // pre-remove set: revert the optimistic UI to it.
      setDraft((d) => (d ? { ...d, attachments: attachmentsRef.current } : d));
    }
  };

  const dismissUpload = (localId: string) =>
    setUploads((u) => u.filter((e) => e.local_id !== localId));

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    void onFilesChosen(e.dataTransfer?.files ?? null);
  };

  return {
    uploads,
    dragActive,
    setDragActive,
    fileInputRef,
    onFilesChosen,
    onRemoveAttachment,
    dismissUpload,
    onDrop,
  };
}

type AttachmentsSectionProps = {
  attachments: DraftAttachment[];
  uploads: PendingUpload[];
  dragActive: boolean;
  setDragActive: (v: boolean) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onFilesChosen: (files: FileList | File[] | null) => void;
  onRemoveAttachment: (attachmentId: string) => void;
  dismissUpload: (localId: string) => void;
  onDrop: (e: React.DragEvent) => void;
};

export function AttachmentsSection({
  attachments,
  uploads,
  dragActive,
  setDragActive,
  fileInputRef,
  onFilesChosen,
  onRemoveAttachment,
  dismissUpload,
  onDrop,
}: AttachmentsSectionProps) {
  return (
    <div
      style={dragActive ? dropZoneActiveStyle : dropZoneStyle}
      onDragOver={(e) => {
        e.preventDefault();
        if (!dragActive) setDragActive(true);
      }}
      onDragEnter={(e) => {
        e.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={(e) => {
        // Only clear when the pointer actually leaves the zone, not when it
        // crosses onto a child element inside it.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setDragActive(false);
        }
      }}
      onDrop={onDrop}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        aria-label="Attach files"
        onChange={(e) => {
          void onFilesChosen(e.target.files);
          // Reset so choosing the same file twice fires change again.
          e.target.value = "";
        }}
      />
      <div style={dropHintStyle}>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          style={secondaryButtonStyle}
        >
          Attach files
        </button>
        <span style={{ color: "#888", fontSize: 12 }}>
          or drag &amp; drop &middot; up to 25 MB total (Gmail&apos;s limit)
        </span>
      </div>

      {(attachments.length > 0 || uploads.length > 0) && (
        <ul style={attachmentListStyle} aria-label="Attachments">
          {attachments.map((a) => (
            <li key={a.attachment_id} style={attachmentItemStyle}>
              <span style={attachmentIconStyle}>📎</span>
              <span style={attachmentNameStyle} title={a.filename}>
                {a.filename}
              </span>
              {a.size ? (
                <span style={attachmentMetaStyle}>{formatBytes(a.size)}</span>
              ) : null}
              <button
                type="button"
                onClick={() => onRemoveAttachment(a.attachment_id)}
                style={attachmentRemoveStyle}
                aria-label={`Remove ${a.filename}`}
              >
                ✕
              </button>
            </li>
          ))}
          {uploads.map((u) => (
            <li
              key={u.local_id}
              style={{
                ...attachmentItemStyle,
                ...(u.status === "error" ? attachmentErrorItemStyle : {}),
              }}
            >
              <span style={attachmentIconStyle}>
                {u.status === "error" ? "⚠️" : "⏳"}
              </span>
              <span style={attachmentNameStyle} title={u.filename}>
                {u.filename}
              </span>
              <span style={attachmentMetaStyle}>
                {u.status === "error"
                  ? (u.error ?? "Failed")
                  : u.status === "reading"
                    ? "Reading…"
                    : "Uploading…"}
              </span>
              {u.status === "error" && (
                <button
                  type="button"
                  onClick={() => dismissUpload(u.local_id)}
                  style={attachmentRemoveStyle}
                  aria-label={`Dismiss ${u.filename}`}
                >
                  ✕
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const dropZoneStyle: React.CSSProperties = {
  marginTop: 10,
  padding: 10,
  border: "1px dashed #d1d5db",
  borderRadius: 6,
  background: "#fafafa",
  transition: "background 0.12s, border-color 0.12s",
};

const dropZoneActiveStyle: React.CSSProperties = {
  ...dropZoneStyle,
  borderColor: "#3b82f6",
  background: "#eff6ff",
};

const dropHintStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  flexWrap: "wrap",
};

const attachmentListStyle: React.CSSProperties = {
  listStyle: "none",
  margin: "10px 0 0",
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const attachmentItemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 8px",
  borderRadius: 4,
  background: "#fff",
  border: "1px solid #eee",
  fontSize: 13,
};

const attachmentErrorItemStyle: React.CSSProperties = {
  background: "#fef2f2",
  border: "1px solid #fecaca",
};

const attachmentIconStyle: React.CSSProperties = {
  flexShrink: 0,
  fontSize: 13,
};

const attachmentNameStyle: React.CSSProperties = {
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "#111",
};

const attachmentMetaStyle: React.CSSProperties = {
  flexShrink: 0,
  color: "#888",
  fontSize: 12,
};

const attachmentRemoveStyle: React.CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  border: "none",
  color: "#991b1b",
  cursor: "pointer",
  fontSize: 13,
  lineHeight: 1,
  padding: 2,
};
