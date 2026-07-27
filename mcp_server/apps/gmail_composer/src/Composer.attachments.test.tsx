import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Composer, type Draft } from "./Composer";

function makeMcpApp(callResult: unknown = null) {
  const callServerTool = vi.fn(async () => callResult);
  const updateModelContext = vi.fn(
    async (_args: { content: Array<{ type: "text"; text: string }> }) => ({}),
  );
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
    updateModelContext,
  };
  return { app, callServerTool, updateModelContext };
}

// A save_draft mock that echoes the attachment set it was given back as
// persisted rows (uploads -> a minted server id, refs -> preserved), so the
// component's attachmentsRef tracks committed server state across serialized
// ops - the exact thing the concurrency tests need to exercise.
function makeEchoingMcpApp() {
  const calls: { name: string; args: Record<string, any> }[] = []; // eslint-disable-line @typescript-eslint/no-explicit-any
  const callServerTool = vi.fn(
    async ({ name, arguments: args }: { name: string; arguments: Record<string, any> }) => {   // eslint-disable-line @typescript-eslint/no-explicit-any
      calls.push({ name, args });
      if (name === "gmail_composer.save_draft") {
        const atts = ((args.attachments as any[]) ?? []).map((a) =>   // eslint-disable-line @typescript-eslint/no-explicit-any
          a.data_base64 !== undefined
            ? { attachment_id: `srv-${a.filename}`, filename: a.filename, mime_type: a.mime_type, size: 1 }
            : { attachment_id: a.attachment_id, filename: String(a.attachment_id).replace(/^srv-/, ""), size: 1 },
        );
        return {
          structuredContent: {
            draft_id: args.draft_id,
            to: args.to,
            subject: args.subject,
            body: args.body,
            attachments: atts,
          },
        };
      }
      return { structuredContent: {} };
    },
  );
  const updateModelContext = vi.fn(
    async (_args: { content: Array<{ type: "text"; text: string }> }) => ({}),
  );
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
    updateModelContext,
  };
  return { app, callServerTool, calls };
}

const sampleDraft: Draft = {
  draft_id: "d-1",
  from: "alice@example.com",
  to: "bob@example.com",
  subject: "Hello",
  body: "Hi Bob",
};

describe("Composer attachments", () => {
  it("renders existing draft attachments with a remove control", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [
            {
              attachment_id: "att-1",
              filename: "report.pdf",
              mime_type: "application/pdf",
              size: 2048,
            },
          ],
        },
      });
    });
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /remove report\.pdf/i }),
    ).toBeInTheDocument();
  });

  it("uploads a chosen file via save_draft with base64 and keeps existing refs", async () => {
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [{ attachment_id: "att-existing", filename: "old.pdf", size: 10 }],
        },
      });
    });
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => {
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      // Existing file preserved by reference; new file uploaded as base64
      // ("hello" -> aGVsbG8=).
      expect(save?.args.attachments).toEqual([
        { attachment_id: "att-existing" },
        { filename: "hello.txt", mime_type: "text/plain", data_base64: "aGVsbG8=" },
      ]);
    });
    expect(await screen.findByText("hello.txt")).toBeInTheDocument();
  });

  it("rejects an oversized file without calling save_draft", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    const big = new File(["x"], "big.zip", { type: "application/zip" });
    Object.defineProperty(big, "size", { value: 26 * 1000 * 1000 });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [big] } });
    });
    expect(callServerTool).not.toHaveBeenCalled();
    expect(await screen.findByText(/too large/i)).toBeInTheDocument();
  });

  it("removes an existing attachment via save_draft without its id", async () => {
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [
            { attachment_id: "att-1", filename: "keep.pdf" },
            { attachment_id: "att-2", filename: "drop.pdf" },
          ],
        },
      });
    });
    fireEvent.click(screen.getByRole("button", { name: /remove drop\.pdf/i }));
    await waitFor(() => {
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      expect(save?.args.attachments).toEqual([{ attachment_id: "att-1" }]);
    });
  });

  it("an attachment save uses the latest body, not a pre-read snapshot", async () => {
    // Regression: the attachment path must send the freshest text, not the text
    // snapshotted before the async file read (which would revert a live edit).
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft }); // body "Hi Bob"
    });
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: "Hi Bob EDITED" },
    });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const file = new File(["x"], "note.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => {
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      expect(save?.args.body).toBe("Hi Bob EDITED");
    });
  });

  it("serializes concurrent uploads so neither file is dropped", async () => {
    // Regression: two quick drops each built their whole-set replace from the
    // same stale draft, so the second clobbered the first's file.
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: { ...sampleDraft, attachments: [] } });
    });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const fileA = new File(["AAA"], "a.txt", { type: "text/plain" });
    const fileB = new File(["BBB"], "b.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [fileA] } });
      fireEvent.change(input, { target: { files: [fileB] } });
    });
    await waitFor(() => {
      const saves = calls.filter((c) => c.name === "gmail_composer.save_draft");
      expect(saves.length).toBe(2);
      // The second save carries BOTH files (a ref to the first + the second),
      // proving it built on the committed result of the first, not a stale draft.
      expect(saves[saves.length - 1].args.attachments).toHaveLength(2);
    });
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
      expect(screen.getByText("b.txt")).toBeInTheDocument();
    });
  });

  it("does not autosave a draft that is being discarded (no resurrection)", async () => {
    vi.useFakeTimers();
    try {
      // Hold the discard RPC in-flight so the composer stays mounted while the
      // user keeps typing.
      let resolveDiscard: (v: unknown) => void = () => {};
      const discardPromise = new Promise((r) => {
        resolveDiscard = r;
      });
      const callServerTool = vi.fn((a: { name: string }) =>
        a.name === "gmail_composer.discard"
          ? discardPromise
          : Promise.resolve({ structuredContent: {} }),
      );
      const app = {
        ontoolresult: undefined as ((raw: unknown) => void) | undefined,
        callServerTool,
        updateModelContext: vi.fn(async () => ({})),
      };
      render(<Composer mcpApp={app} />);
      act(() => {
        app.ontoolresult?.({ structuredContent: sampleDraft });
      });
      fireEvent.click(screen.getByRole("button", { name: /^discard$/i }));
      fireEvent.click(screen.getByRole("button", { name: /yes, discard/i }));
      // Discard is in flight; typing here must NOT arm an autosave.
      fireEvent.change(screen.getByLabelText("Body"), {
        target: { value: "typed while discarding" },
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(900);
      });
      await act(async () => {
        resolveDiscard({ structuredContent: { discarded: true } });
      });
      const savedDraft = callServerTool.mock.calls.some(
        (c) => (c[0] as { name: string }).name === "gmail_composer.save_draft",
      );
      expect(savedDraft).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a zero-byte file without calling save_draft", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    const empty = new File([], "empty.txt", { type: "text/plain" });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [empty] } });
    });
    expect(callServerTool).not.toHaveBeenCalled();
    expect(await screen.findByText(/empty file/i)).toBeInTheDocument();
  });

  it("persists a just-dropped attachment before sending (send waits for the read)", async () => {
    // Regression: the read used to run outside the serialized chain, so a Send
    // clicked mid-read raced ahead and the message went out without the file.
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: { ...sampleDraft, attachments: [] } });
    });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const file = new File(["x"], "late.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
      fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    });
    await waitFor(() => {
      expect(calls.some((c) => c.name === "gmail_composer.send")).toBe(true);
    });
    const saveIdx = calls.findIndex((c) => c.name === "gmail_composer.save_draft");
    const sendIdx = calls.findIndex((c) => c.name === "gmail_composer.send");
    expect(saveIdx).toBeGreaterThanOrEqual(0);
    expect(saveIdx).toBeLessThan(sendIdx);
  });

  it("double-clicking Send fires only one send RPC", async () => {
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    const sendBtn = screen.getByRole("button", { name: /^send$/i });
    await act(async () => {
      fireEvent.click(sendBtn);
      fireEvent.click(sendBtn);
    });
    await waitFor(() => {
      expect(calls.filter((c) => c.name === "gmail_composer.send").length).toBe(1);
    });
  });

  it("does not attach against a draft that is being sent", async () => {
    let resolveSend: (v: unknown) => void = () => {};
    const sendPromise = new Promise((r) => {
      resolveSend = r;
    });
    const seen: string[] = [];
    const callServerTool = vi.fn((a: { name: string }) => {
      seen.push(a.name);
      return a.name === "gmail_composer.send"
        ? sendPromise
        : Promise.resolve({ structuredContent: {} });
    });
    const app = {
      ontoolresult: undefined as ((raw: unknown) => void) | undefined,
      callServerTool,
      updateModelContext: vi.fn(async () => ({})),
    };
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    // Send is now in flight (closingRef is set); a file chosen now must not
    // enqueue a save against the terminal draft.
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const file = new File(["x"], "late.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    expect(seen.includes("gmail_composer.save_draft")).toBe(false);
    await act(async () => {
      resolveSend({ structuredContent: { message_id: "m" } });
    });
  });

  it("fails the size preflight closed when an existing attachment size is unknown", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          // No size on the existing file: must be treated as a full 25 MB, not 0.
          attachments: [{ attachment_id: "att-x", filename: "unknown.bin" }],
        },
      });
    });
    const file = new File(["x"], "small.txt", { type: "text/plain" });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    // Unknown existing size counts as the full cap, so the preflight refuses the
    // batch before issuing any save_draft.
    expect(callServerTool).not.toHaveBeenCalled();
    expect(await screen.findByText(/exceed/i)).toBeInTheDocument();
  });
});
