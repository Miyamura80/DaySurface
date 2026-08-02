import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
  const draft: ComposerDraft = {
    draft_id: "d1",
    to: "margaret@example.com",
    subject: "Re: NDA",
    body: "Hi Margaret,",
    thread_id: "t1",
    attachments,
  };
  render(
    <InlineComposer
      draft={draft}
      thread={null}
      mcpApp={app}
      onDraftChange={vi.fn()}
      onBack={vi.fn()}
      onDiscard={vi.fn()}
      onSent={vi.fn()}
    />,
  );
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
