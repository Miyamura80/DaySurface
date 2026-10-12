import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InlineComposer } from "./InlineComposer";
import { MAX_ATTACHMENT_BYTES } from "./useComposerAttachments";
import type { ComposerDraft, DraftAttachment, McpAppLike } from "./types";

/** A File whose reported size we control without allocating the bytes. */
function fileOfSize(name: string, size: number, type = "application/pdf"): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

function makeMcpApp() {
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const callServerTool = vi.fn(async (args: { name: string; arguments: Record<string, unknown> }) => {
    calls.push(args);
    return null;
  });
  const app: McpAppLike = { callServerTool, openLink: vi.fn(async () => ({})) };
  return { app, calls };
}

function renderComposer(app: McpAppLike, attachments?: DraftAttachment[]) {
  const mk = (atts?: DraftAttachment[]): ComposerDraft => ({
    draft_id: "d1",
    to: "margaret@example.com",
    subject: "Re: NDA",
    body: "Hi Margaret,",
    thread_id: "t1",
    attachments: atts,
  });
  const view = (draft: ComposerDraft) => (
    <InlineComposer
      draft={draft}
      thread={null}
      mcpApp={app}
      onDraftChange={vi.fn()}
      onBack={vi.fn()}
      onDiscard={vi.fn()}
      onSent={vi.fn()}
    />
  );
  const r = render(view(mk(attachments)));
  // Re-render with a new server-confirmed attachment set, as a save echo would.
  return { rerender: (atts: DraftAttachment[]) => r.rerender(view(mk(atts))) };
}

/** The hidden <input type=file> the paperclip button proxies to. */
function fileInput(): HTMLInputElement {
  const el = document.querySelector('input[type="file"]');
  if (!el) throw new Error("file input not found");
  return el as HTMLInputElement;
}

function drop(files: File[]) {
  fireEvent.change(fileInput(), { target: { files } });
}

describe("composer attachment preflight", () => {
  afterEach(() => vi.clearAllMocks());

  it("refuses an oversized file without reading it", () => {
    const { app, calls } = makeMcpApp();
    renderComposer(app);

    drop([fileOfSize("huge.pdf", MAX_ATTACHMENT_BYTES + 1)]);

    expect(screen.getByText("huge.pdf")).toBeTruthy();
    expect(screen.getByText(/Too large/)).toBeTruthy();
    expect(calls.filter((c) => c.name === "gmail_composer.save_draft")).toHaveLength(0);
  });

  it("refuses a zero-byte file", () => {
    const { app } = makeMcpApp();
    renderComposer(app);

    drop([fileOfSize("empty.pdf", 0)]);

    expect(screen.getByText(/Empty file/)).toBeTruthy();
  });

  it("accepts the good files in a mixed batch and refuses only the bad one", () => {
    const { app } = makeMcpApp();
    renderComposer(app);

    drop([fileOfSize("ok.pdf", 1_000), fileOfSize("huge.pdf", MAX_ATTACHMENT_BYTES + 1)]);

    expect(screen.getByText(/Too large/)).toBeTruthy();
    // "ok.pdf" is mid-read (FileReader is async); the rejection is immediate and
    // must not have taken the whole batch down with it.
    expect(screen.queryByText(/Empty file/)).toBeNull();
  });

  it("refuses a batch that individually fits but collectively exceeds the cap", () => {
    const { app } = makeMcpApp();
    renderComposer(app);

    const half = Math.floor(MAX_ATTACHMENT_BYTES * 0.6);
    drop([fileOfSize("a.pdf", half), fileOfSize("b.pdf", half)]);

    const errs = screen.getAllByText(/would exceed/);
    expect(errs).toHaveLength(2);
  });

  it("counts already-attached files toward the cap", () => {
    const { app } = makeMcpApp();
    renderComposer(app, [
      { filename: "existing.pdf", mime_type: "application/pdf", size: MAX_ATTACHMENT_BYTES - 500 },
    ]);

    drop([fileOfSize("small.pdf", 1_000)]);

    expect(screen.getByText(/would exceed/)).toBeTruthy();
  });

  it("fails closed when an existing attachment has no known size", () => {
    const { app } = makeMcpApp();
    // size omitted: treating it as 0 would undercount and let the batch through.
    renderComposer(app, [{ filename: "unknown.pdf", mime_type: "application/pdf" }]);

    drop([fileOfSize("small.pdf", 1_000)]);

    expect(screen.getByText(/would exceed/)).toBeTruthy();
  });

  it("counts a still-reading batch toward the cap of the next pick", () => {
    const { app } = makeMcpApp();
    renderComposer(app);

    // First batch is accepted and its FileReader is left unresolved (jsdom
    // reads are async), so these bytes are not yet in `attachments`.
    const big = Math.floor(MAX_ATTACHMENT_BYTES * 0.7);
    drop([fileOfSize("first.pdf", big)]);
    expect(screen.queryByText(/would exceed/)).toBeNull();

    // Second pick, made during that read window, must still see the claimed
    // bytes - otherwise the cumulative check fails open.
    drop([fileOfSize("second.pdf", big)]);
    expect(screen.getByText(/would exceed/)).toBeTruthy();
  });

  it("lets the user dismiss a rejection", () => {
    const { app } = makeMcpApp();
    renderComposer(app);

    drop([fileOfSize("huge.pdf", MAX_ATTACHMENT_BYTES + 1)]);
    expect(screen.getByText(/Too large/)).toBeTruthy();

    fireEvent.click(screen.getByTitle("Dismiss"));
    expect(screen.queryByText(/Too large/)).toBeNull();
  });
});

/**
 * A fake save/send backend with Gmail's id behaviour: every write rebuilds the
 * draft and hands each file a fresh attachment_id, and a ref to an id that is
 * no longer on the draft is refused. With `gated`, each write waits for
 * `release()`, so a test can hold one on the wire.
 */
function makeServer({ gated = false, sizes = {} as Record<string, number> } = {}) {
  let n = 0;
  let files: DraftAttachment[] = [];
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const gates: Array<() => void> = [];
  const callServerTool = vi.fn(async (req: { name: string; arguments: Record<string, unknown> }) => {
    calls.push(req);
    const write = req.name === "gmail_composer.save_draft" || req.name === "gmail_composer.send";
    if (!write) return null;
    if (gated) await new Promise<void>((r) => gates.push(r));
    const wanted = req.arguments.attachments as Array<Record<string, string>> | undefined;
    if (wanted) {
      files = wanted.map((item) => {
        if (!item.attachment_id) {
          return { filename: item.filename, mime_type: item.mime_type, size: sizes[item.filename] ?? 1 };
        }
        const kept = files.find((f) => f.attachment_id === item.attachment_id);
        if (!kept) throw new Error(`attachment_id ${item.attachment_id} is not on draft`);
        return kept;
      });
    }
    files = files.map((f) => ({ ...f, attachment_id: `att-${++n}`, message_id: `msg-${n}` }));
    if (req.name === "gmail_composer.send") return { structuredContent: { message_id: "sent-1" } };
    return { structuredContent: { draft_id: "d1", attachments: files } };
  });
  const app: McpAppLike = { callServerTool, openLink: vi.fn(async () => ({})) };
  const writes = () => calls.filter((c) => c.name === "gmail_composer.save_draft");
  const release = async () => {
    await act(async () => {});
    const g = gates.shift();
    if (!g) throw new Error("no write on the wire");
    await act(async () => { g(); });
  };
  return { app, calls, writes, release };
}

const autosave = () => act(async () => { vi.advanceTimersByTime(800); });

async function pickAndWait(name: string, size = 1_000) {
  drop([fileOfSize(name, size)]);
  await waitFor(() => expect(screen.getByText(name)).toBeTruthy());
}

function typeBody(value: string) {
  fireEvent.change(screen.getByLabelText("Body"), { target: { value } });
}

describe("composer attachment persistence (#32)", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("uploads a picked file once, then keeps it by reference", async () => {
    const { app, writes, calls } = makeServer();
    renderComposer(app);

    await pickAndWait("a.pdf");
    await autosave();
    expect(writes()[0].arguments.attachments).toEqual([
      expect.objectContaining({ filename: "a.pdf", data_base64: expect.any(String) }),
    ]);

    typeBody("more");
    await autosave();
    expect(writes()[1].arguments.attachments).toEqual([{ attachment_id: "att-1" }]);

    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("Message sent")).toBeTruthy());
    const send = calls.find((c) => c.name === "gmail_composer.send");
    expect(send?.arguments.attachments).toEqual([{ attachment_id: "att-2" }]);
  });

  it("does not grow a save's payload with the number of saves", async () => {
    const { app, writes } = makeServer();
    renderComposer(app);

    await pickAndWait("a.pdf");
    for (const body of ["one", "two", "three", "four"]) {
      typeBody(body);
      await autosave();
    }
    const sizes = writes().slice(1).map((w) => JSON.stringify(w.arguments.attachments).length);
    expect(new Set(sizes).size).toBe(1);
    expect(JSON.stringify(writes().slice(1)).includes("data_base64")).toBe(false);
  });

  it("serializes concurrent writes so neither file is dropped", async () => {
    const { app, writes, release } = makeServer({ gated: true });
    renderComposer(app);

    await pickAndWait("a.pdf");
    await autosave();
    expect(writes()).toHaveLength(1);

    // A second pick while the first save is on the wire must wait for it.
    await pickAndWait("b.pdf");
    await autosave();
    expect(writes()).toHaveLength(1);

    await release();
    await act(async () => {});
    expect(writes()).toHaveLength(2);
    expect(writes()[1].arguments.attachments).toEqual([
      { attachment_id: "att-1" },
      expect.objectContaining({ filename: "b.pdf", data_base64: expect.any(String) }),
    ]);
    await release();
    expect(screen.getByText("a.pdf")).toBeTruthy();
    expect(screen.getByText("b.pdf")).toBeTruthy();
  });

  it("removes a confirmed file by leaving it out of the next save", async () => {
    const { app, writes } = makeServer();
    renderComposer(app);

    await pickAndWait("a.pdf");
    await autosave();
    fireEvent.click(screen.getByTitle("Remove"));
    await autosave();

    expect(writes()[1].arguments.attachments).toEqual([]);
    expect(screen.queryByText("a.pdf")).toBeNull();
  });

  it("references a draft's existing files by their latest ids after a text-only save", async () => {
    const { app, writes } = makeServer();
    renderComposer(app);

    // A text edit first: attachments are omitted, but the server still
    // rebuilds the draft. The next explicit set must cite the new ids.
    typeBody("edit");
    await autosave();
    await pickAndWait("a.pdf");
    await autosave();
    typeBody("edit again");
    await autosave();

    expect(screen.queryByText(/is not on draft/)).toBeNull();
    expect(writes()[2].arguments.attachments).toEqual([{ attachment_id: "att-1" }]);
  });

  it("counts a confirmed upload once toward the cap", async () => {
    const big = Math.floor(MAX_ATTACHMENT_BYTES * 0.6);
    const { app } = makeServer({ sizes: { "big.pdf": big } });
    renderComposer(app);

    await pickAndWait("big.pdf", big);
    await autosave();
    drop([fileOfSize("small.pdf", 1_000)]);

    expect(screen.queryByText(/would exceed/)).toBeNull();
  });
});
