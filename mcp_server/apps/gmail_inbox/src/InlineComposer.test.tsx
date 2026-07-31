import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { InlineComposer } from "./InlineComposer";
import type { ComposerDraft, McpAppLike } from "./types";

type Deferred = {
  promise: Promise<unknown>;
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
};

function deferred(): Deferred {
  let resolve!: (v: unknown) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<unknown>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/**
 * An mcpApp whose save_draft/send calls hang until the test resolves them, so a
 * test can land the two replies in whichever order it wants to exercise.
 */
function makeMcpApp() {
  const pending: Record<string, Deferred[]> = {};
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const callServerTool = vi.fn(
    (args: { name: string; arguments: Record<string, unknown> }) => {
      calls.push(args);
      const d = deferred();
      (pending[args.name] ||= []).push(d);
      return d.promise;
    },
  );
  const take = (name: string) => {
    const d = pending[name]?.shift();
    if (!d) throw new Error(`no pending ${name} call to settle`);
    return d;
  };
  const settle = async (name: string, value: unknown) => {
    const d = take(name);
    await act(async () => { d.resolve(value); await d.promise.catch(() => {}); });
  };
  const fail = async (name: string, message: string) => {
    const d = take(name);
    await act(async () => { d.reject(new Error(message)); await d.promise.catch(() => {}); });
  };
  const countOf = (name: string) => calls.filter((c) => c.name === name).length;
  return { app: { callServerTool } as unknown as McpAppLike, calls, settle, fail, countOf };
}

const draft: ComposerDraft = {
  draft_id: "d1",
  to: "margaret@example.com",
  subject: "Re: NDA",
  body: "Hi Margaret,",
  thread_id: "t1",
};

const sendOk = { structuredContent: { message_id: "m123", thread_id: "t1" } };

function renderComposer(app: McpAppLike, onSent = vi.fn()) {
  render(
    <InlineComposer
      draft={draft}
      thread={null}
      mcpApp={app}
      onDraftChange={vi.fn()}
      onBack={vi.fn()}
      onDiscard={vi.fn()}
      onSent={onSent}
    />,
  );
  return onSent;
}

/** Type in the body, then let the 800ms autosave debounce fire. */
async function editAndFireAutosave() {
  fireEvent.change(screen.getByLabelText("Body"), {
    target: { value: "Hi Margaret,\n\nOne more thing." },
  });
  await act(async () => { vi.advanceTimersByTime(800); });
}

describe("InlineComposer send/autosave race", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("keeps the sent panel when an in-flight autosave replies after the send", async () => {
    const { app, settle, countOf } = makeMcpApp();
    renderComposer(app);

    // Autosave is on the wire and deliberately left unresolved.
    await editAndFireAutosave();
    expect(countOf("gmail_composer.save_draft")).toBe(1);

    fireEvent.click(screen.getByText("Send"));
    await settle("gmail_composer.send", sendOk);
    expect(screen.getByText("Message sent")).toBeTruthy();

    // The straggler lands last and reports success - it must stay silent.
    await settle("gmail_composer.save_draft", { structuredContent: { draft_id: "d1" } });

    expect(screen.getByText("Message sent")).toBeTruthy();
    expect(screen.queryByLabelText("Body")).toBeNull();
  });

  it("keeps the sent panel when the straggling autosave fails", async () => {
    const { app, settle, fail } = makeMcpApp();
    renderComposer(app);

    await editAndFireAutosave();
    fireEvent.click(screen.getByText("Send"));
    await settle("gmail_composer.send", sendOk);

    // The outstanding save_draft errors out; that error must not steal the UI.
    await fail("gmail_composer.save_draft", "network blip");

    expect(screen.getByText("Message sent")).toBeTruthy();
    expect(screen.queryByLabelText("Body")).toBeNull();
  });

  it("does not fire a second send while one is in flight", async () => {
    const { app, settle, countOf } = makeMcpApp();
    renderComposer(app);

    const send = screen.getByText("Send");
    fireEvent.click(send);
    fireEvent.click(send);
    fireEvent.click(send);

    expect(countOf("gmail_composer.send")).toBe(1);
    await settle("gmail_composer.send", sendOk);
    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("stays editable and retryable when the server does not confirm the send", async () => {
    const { app, settle, countOf } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    // A tool-level failure resolves the call but carries no message_id.
    await settle("gmail_composer.send", { isError: true, content: [{ type: "text", text: "nope" }] });

    expect(screen.queryByText("Message sent")).toBeNull();
    expect(screen.getByLabelText("Body")).toBeTruthy();

    // closingRef was released, so the user can try again.
    fireEvent.click(screen.getByText("Send"));
    expect(countOf("gmail_composer.send")).toBe(2);
    await settle("gmail_composer.send", sendOk);
    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("calls onSent 1.5s after a confirmed send", async () => {
    const { app, settle } = makeMcpApp();
    const onSent = renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    await settle("gmail_composer.send", sendOk);
    expect(onSent).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(onSent).toHaveBeenCalledTimes(1);
  });
});
