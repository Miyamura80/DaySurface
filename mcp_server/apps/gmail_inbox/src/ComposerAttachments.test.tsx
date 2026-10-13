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

type Call = { name: string; arguments: Record<string, unknown> };
type Mode = "ok" | "lose-reply" | "fail";

/**
 * A fake composer backend with Gmail's id behaviour: every write rebuilds the
 * draft and hands each file a fresh attachment_id, and a remove citing an id no
 * longer on the draft is refused. `modes` makes the next call to a tool apply
 * then lose its reply ("lose-reply") or fail without applying ("fail");
 * `rename` changes a file's name on echo, as a MIME round-trip can.
 */
function makeServer(
  opts: { gated?: boolean; files?: DraftAttachment[]; rename?: Record<string, string>; sizes?: Record<string, number> } = {},
) {
  let n = 0;
  let files: DraftAttachment[] = [...(opts.files ?? [])];
  const calls: Call[] = [];
  const gates: Array<() => void> = [];
  const modes: Record<string, Mode[]> = {};
  const rotate = () => { files = files.map((f) => ({ ...f, attachment_id: `att-${++n}`, message_id: `msg-${n}` })); };
  const apply = (req: Call) => {
    const a = req.arguments;
    if (req.name === "gmail_composer.add_attachment") {
      const name = String(a.filename);
      files = [...files, { filename: opts.rename?.[name] ?? name, mime_type: String(a.mime_type), size: opts.sizes?.[name] ?? 1 }];
    } else if (req.name === "gmail_composer.remove_attachment") {
      if (!files.some((f) => f.attachment_id === a.attachment_id)) {
        throw new Error(`attachment_id ${a.attachment_id} is not on draft`);
      }
      files = files.filter((f) => f.attachment_id !== a.attachment_id);
    }
    rotate();
  };
  const callServerTool = vi.fn(async (req: Call) => {
    calls.push(req);
    if (req.name === "gmail_composer.refresh") {
      if (modes[req.name]?.shift() === "fail") throw new Error("offline");
      return { structuredContent: { draft_id: "d1", attachments: files } };
    }
    const writes = ["save_draft", "send", "add_attachment", "remove_attachment"].map((t) => `gmail_composer.${t}`);
    if (!writes.includes(req.name)) return null;
    if (opts.gated) await new Promise<void>((r) => gates.push(r));
    const mode = modes[req.name]?.shift() ?? "ok";
    if (mode === "fail") return { isError: true, content: [{ type: "text", text: "gmail said no" }] };
    apply(req);
    if (mode === "lose-reply") throw new Error("request timed out");
    if (req.name === "gmail_composer.send") return { structuredContent: { message_id: "sent-1" } };
    return { structuredContent: { draft_id: "d1", attachments: files } };
  });
  const app: McpAppLike = { callServerTool, openLink: vi.fn(async () => ({})) };
  const named = (tool: string) => calls.filter((c) => c.name === `gmail_composer.${tool}`);
  const release = async () => {
    await act(async () => {});
    const g = gates.shift();
    if (!g) throw new Error("no write on the wire");
    await act(async () => { g(); });
  };
  const next = (tool: string, mode: Mode) => { (modes[`gmail_composer.${tool}`] ||= []).push(mode); };
  return { app, calls, named, release, next, files: () => files, rotate };
}

const autosave = () => act(async () => { vi.advanceTimersByTime(800); });
const settled = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

async function pick(name: string, size = 1_000) {
  drop([fileOfSize(name, size)]);
  await waitFor(() => expect(screen.getByText(name)).toBeTruthy());
  await settled();
}

function typeBody(value: string) {
  fireEvent.change(screen.getByLabelText("Body"), { target: { value } });
}

describe("composer attachment writes (#32)", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("uploads a pick exactly once; text saves and send never carry files", async () => {
    const { app, calls, named } = makeServer();
    renderComposer(app);

    await pick("a.pdf");
    for (const body of ["one", "two", "three"]) {
      typeBody(body);
      await autosave();
    }
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("Message sent")).toBeTruthy());

    expect(named("add_attachment")).toHaveLength(1);
    expect(calls.filter((c) => "data_base64" in c.arguments)).toHaveLength(1);
    for (const c of [...named("save_draft"), ...named("send")]) {
      expect(c.arguments).not.toHaveProperty("attachments");
    }
  });

  it("runs writes one at a time so neither of two quick picks is lost", async () => {
    const { app, named, release, files } = makeServer({ gated: true });
    renderComposer(app);

    drop([fileOfSize("a.pdf", 1_000)]);
    await waitFor(() => expect(named("add_attachment")).toHaveLength(1));
    drop([fileOfSize("b.pdf", 1_000)]);
    await waitFor(() => expect(screen.getByText("b.pdf")).toBeTruthy());
    await settled();
    expect(named("add_attachment")).toHaveLength(1);

    await release();
    await release();
    expect(files().map((f) => f.filename)).toEqual(["a.pdf", "b.pdf"]);
    expect(screen.getByText("a.pdf")).toBeTruthy();
    expect(screen.getByText("b.pdf")).toBeTruthy();
  });

  it("removes a file by its latest id, after a text save replaced every id", async () => {
    const { app, named, files } = makeServer({ files: [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }] });
    renderComposer(app, [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }]);

    typeBody("edit");
    await autosave();
    fireEvent.click(screen.getByTitle("Remove"));
    await settled();

    expect(named("remove_attachment")).toHaveLength(1);
    expect(files()).toEqual([]);
    expect(screen.queryByText("x.pdf")).toBeNull();
    expect(screen.queryByText(/Couldn't remove/)).toBeNull();
  });

  it("re-reads and retries a remove whose id went stale behind its back", async () => {
    const { app, named, files, rotate } = makeServer({ files: [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }] });
    renderComposer(app, [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }]);

    rotate(); // an agent edit the composer never heard about
    fireEvent.click(screen.getByTitle("Remove"));
    await settled();

    expect(named("remove_attachment")).toHaveLength(2);
    expect(files()).toEqual([]);
    expect(screen.queryByText(/Couldn't remove/)).toBeNull();
  });

  it("shows a pick once when its upload landed but the reply was lost", async () => {
    const { app, next, files } = makeServer();
    renderComposer(app);

    next("add_attachment", "lose-reply");
    await pick("a.pdf");

    expect(files()).toHaveLength(1);
    expect(screen.getAllByText("a.pdf")).toHaveLength(1);
    expect(screen.queryByText(/Upload failed/)).toBeNull();
  });

  it("reports a pick the server refused and does not show it as attached", async () => {
    const { app, next, files } = makeServer();
    renderComposer(app);

    next("add_attachment", "fail");
    drop([fileOfSize("a.pdf", 1_000)]);
    await waitFor(() => expect(screen.getByText(/Upload failed: gmail said no/)).toBeTruthy());

    expect(files()).toEqual([]);
    expect(screen.queryByTitle("Remove")).toBeNull();
  });

  it("does not duplicate a file whose name changes on the way back", async () => {
    const { app, named, files } = makeServer({ rename: { "résumé.pdf": "re\u0301sume\u0301.pdf" } });
    renderComposer(app);

    drop([fileOfSize("résumé.pdf", 1_000)]);
    await waitFor(() => expect(named("add_attachment")).toHaveLength(1));
    await settled();
    typeBody("edit");
    await autosave();

    expect(named("add_attachment")).toHaveLength(1);
    expect(files()).toHaveLength(1);
    expect(screen.getAllByTitle("Remove")).toHaveLength(1);
  });

  it("sends after an upload queued before the click, and freezes the form meanwhile", async () => {
    const { app, calls, release } = makeServer({ gated: true });
    renderComposer(app);

    drop([fileOfSize("a.pdf", 1_000)]);
    await waitFor(() => expect(screen.getByText("a.pdf")).toBeTruthy());
    fireEvent.click(screen.getByText("Send"));
    await settled();

    expect((screen.getByLabelText("Body") as HTMLTextAreaElement).readOnly).toBe(true);
    expect((screen.getByTitle("Attach files") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTitle("Remove")).toBeNull();

    await release();
    await release();
    expect(calls.map((c) => c.name).filter((n) => n !== "gmail_composer.get_thread")).toEqual([
      "gmail_composer.add_attachment",
      "gmail_composer.send",
    ]);
    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("counts a confirmed upload once toward the cap", async () => {
    const big = Math.floor(MAX_ATTACHMENT_BYTES * 0.6);
    const { app } = makeServer({ sizes: { "big.pdf": big } });
    renderComposer(app);

    await pick("big.pdf", big);
    expect(screen.queryByText(/would exceed/)).toBeNull();
    drop([fileOfSize("small.pdf", 1_000)]);

    expect(screen.queryByText(/would exceed/)).toBeNull();
  });

  it("holds a send clicked while a file is still being read", async () => {
    const { app, calls } = makeServer();
    renderComposer(app);

    drop([fileOfSize("a.pdf", 1_000)]);
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("Message sent")).toBeTruthy());

    expect(calls.map((c) => c.name).filter((n) => n !== "gmail_composer.get_thread")).toEqual([
      "gmail_composer.add_attachment",
      "gmail_composer.send",
    ]);
  });

  it("removes a renamed file the user dropped while it was uploading", async () => {
    const { app, named, release, files } = makeServer({ gated: true, rename: { "résumé.pdf": "re\u0301sume\u0301.pdf" } });
    renderComposer(app);

    drop([fileOfSize("résumé.pdf", 1_000)]);
    await waitFor(() => expect(named("add_attachment")).toHaveLength(1));
    fireEvent.click(screen.getByTitle("Remove"));
    await release();
    await release();

    expect(named("remove_attachment")).toHaveLength(1);
    expect(files()).toEqual([]);
  });

  it("reports a remove it could not confirm instead of calling it saved", async () => {
    const { app, next, files } = makeServer({ files: [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }] });
    renderComposer(app, [{ filename: "x.pdf", size: 5, attachment_id: "att-0" }]);

    next("remove_attachment", "fail");
    next("refresh", "fail");
    fireEvent.click(screen.getByTitle("Remove"));
    await settled();

    expect(files()).toHaveLength(1);
    expect(screen.getByText(/Couldn't remove x.pdf/)).toBeTruthy();
  });

  it("disables discard while a send is in flight", async () => {
    const { app } = makeServer({ gated: true });
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    await settled();

    expect((screen.getByTitle("Discard draft") as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not take an agent's file for a pick whose add failed", async () => {
    const { app, next, files } = makeServer();
    renderComposer(app);

    next("add_attachment", "fail");
    // The agent attached its own file while the pick's add was failing.
    files().push({ filename: "agent.pdf", size: 1, attachment_id: "att-agent" });
    drop([fileOfSize("mine.pdf", 1_000)]);

    await waitFor(() => expect(screen.getByText(/Upload failed/)).toBeTruthy());
    expect(screen.getByText("agent.pdf")).toBeTruthy();
  });

  it("lets a pick removed mid-read skip its read without an error", async () => {
    const { app, named } = makeServer();
    renderComposer(app);

    drop([fileOfSize("a.pdf", 1_000)]);
    fireEvent.click(screen.getByTitle("Remove"));
    await settled();
    await settled();

    expect(named("add_attachment")).toHaveLength(0);
    expect(screen.queryByText(/Upload failed/)).toBeNull();
  });
});
