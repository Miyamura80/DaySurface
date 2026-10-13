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
  // Writes run on the composer's queue, a microtask after the click.
  const flush = () => act(async () => {});
  const take = (name: string) => {
    const d = pending[name]?.shift();
    if (!d) throw new Error(`no pending ${name} call to settle`);
    return d;
  };
  const settle = async (name: string, value: unknown) => {
    await flush();
    const d = take(name);
    await act(async () => { d.resolve(value); await d.promise.catch(() => {}); });
  };
  const fail = async (name: string, message: string) => {
    await flush();
    const d = take(name);
    await act(async () => { d.reject(new Error(message)); await d.promise.catch(() => {}); });
  };
  const countOf = (name: string) => calls.filter((c) => c.name === name).length;
  const app: McpAppLike = { callServerTool, openLink: vi.fn(async () => ({})) };
  return { app, calls, settle, fail, countOf, flush };
}

const draft: ComposerDraft = {
  draft_id: "d1",
  to: "margaret@example.com",
  subject: "Re: NDA",
  body: "Hi Margaret,",
  thread_id: "t1",
};

const sendOk = { structuredContent: { message_id: "m123", thread_id: "t1" } };
const draftStillThere = { structuredContent: { draft_exists: true } };
const draftGone = { structuredContent: { draft_exists: false, sent: null } };
const recordedSend = {
  structuredContent: { draft_exists: false, sent: { message_id: "m123", thread_id: "t1" } },
};

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

  it("holds the send until an in-flight autosave replies", async () => {
    const { app, settle, countOf, flush } = makeMcpApp();
    renderComposer(app);

    // Autosave is on the wire and deliberately left unresolved.
    await editAndFireAutosave();
    expect(countOf("gmail_composer.save_draft")).toBe(1);

    // Two writes never overlap: the send waits, so it can't race the save.
    fireEvent.click(screen.getByText("Send"));
    await flush();
    expect(countOf("gmail_composer.send")).toBe(0);
    expect(screen.getAllByText("Sending…").length).toBeGreaterThan(0);

    await settle("gmail_composer.save_draft", { structuredContent: { draft_id: "d1" } });
    expect(screen.queryByText(/Saved/)).toBeNull();
    await settle("gmail_composer.send", sendOk);

    expect(screen.getByText("Message sent")).toBeTruthy();
    expect(screen.queryByLabelText("Body")).toBeNull();
  });

  it("still sends when the autosave ahead of it fails", async () => {
    const { app, settle, fail } = makeMcpApp();
    renderComposer(app);

    await editAndFireAutosave();
    fireEvent.click(screen.getByText("Send"));

    // The save's error must not steal the UI from the send queued behind it.
    await fail("gmail_composer.save_draft", "network blip");
    expect(screen.queryByText(/network blip/)).toBeNull();
    await settle("gmail_composer.send", sendOk);

    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("sends the latest typed text, not the text at the time the save was queued", async () => {
    const { app, settle, calls } = makeMcpApp();
    renderComposer(app);

    await editAndFireAutosave();
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "Final words." } });
    fireEvent.click(screen.getByText("Send"));
    await settle("gmail_composer.save_draft", { structuredContent: { draft_id: "d1" } });
    await settle("gmail_composer.send", sendOk);

    const send = calls.find((c) => c.name === "gmail_composer.send");
    expect(send?.arguments.body).toBe("Final words.");
  });

  it("does not fire a second send while one is in flight", async () => {
    const { app, settle, countOf, flush } = makeMcpApp();
    renderComposer(app);

    const send = screen.getByText("Send");
    fireEvent.click(send);
    fireEvent.click(send);
    fireEvent.click(send);

    await flush();
    expect(countOf("gmail_composer.send")).toBe(1);
    await settle("gmail_composer.send", sendOk);
    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("stays editable and retryable when the server does not confirm the send", async () => {
    const { app, settle, countOf, flush } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    // A tool-level failure resolves the call but carries no message_id.
    await settle("gmail_composer.send", { isError: true, content: [{ type: "text", text: "nope" }] });

    expect(screen.queryByText("Message sent")).toBeNull();
    expect(screen.getByLabelText("Body")).toBeTruthy();
    // The server's own reason is shown, not a generic one.
    expect(screen.getByText(/nope/)).toBeTruthy();

    // closingRef was released, so the user can try again.
    fireEvent.click(screen.getByText("Send"));
    await flush();
    expect(countOf("gmail_composer.send")).toBe(2);
    await settle("gmail_composer.send", sendOk);
    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("keeps a failed send's error visible after the pre-send autosave reply", async () => {
    const { app, settle, fail } = makeMcpApp();
    renderComposer(app);

    await editAndFireAutosave();
    fireEvent.click(screen.getByText("Send"));

    // The autosave started before the send replies first; it must stay quiet.
    await settle("gmail_composer.save_draft", { structuredContent: { draft_id: "d1" } });
    // Send fails, so the composer reopens for a retry.
    await fail("gmail_composer.send", "smtp exploded");
    await settle("gmail_composer.send_status", draftStillThere);

    expect(screen.getByLabelText("Body")).toBeTruthy();
    expect(screen.queryByText(/Saved/)).toBeNull();
    expect(screen.getByText(/smtp exploded/)).toBeTruthy();
  });

  it("trusts a server error even if the draft is gone (discarded elsewhere)", async () => {
    const { app, settle, countOf } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    // The draft was discarded by the agent or another tab: send refuses it.
    await settle("gmail_composer.send", {
      isError: true,
      content: [{ type: "text", text: "This draft no longer exists in Gmail." }],
    });

    // A definite failure never probes, so "gone" can't be misread as sent.
    expect(countOf("gmail_composer.send_status")).toBe(0);
    expect(screen.queryByText("Message sent")).toBeNull();
    expect(screen.getByText(/no longer exists/)).toBeTruthy();
  });

  it("shows sent when the send reply is lost but the server recorded the send", async () => {
    const { app, settle, fail } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    // The host times out (or drops the result) after Gmail already sent it.
    await fail("gmail_composer.send", "request timed out");
    await settle("gmail_composer.send_status", recordedSend);

    expect(screen.getByText("Message sent")).toBeTruthy();
    expect(screen.queryByText(/timed out/)).toBeNull();
  });

  it("shows sent when the send resolves without a message_id but was recorded", async () => {
    const { app, settle } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    await settle("gmail_composer.send", { content: [] });
    await settle("gmail_composer.send_status", recordedSend);

    expect(screen.getByText("Message sent")).toBeTruthy();
  });

  it("does not read a gone draft with no sent record as sent", async () => {
    const { app, settle, fail } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    // Lost reply, and the draft is gone, but it may have been discarded.
    await fail("gmail_composer.send", "request timed out");
    await settle("gmail_composer.send_status", draftGone);

    expect(screen.queryByText("Message sent")).toBeNull();
    expect(screen.getByText(/no record of sending it/)).toBeTruthy();
  });

  it("stays editable when the status check itself fails", async () => {
    const { app, fail } = makeMcpApp();
    renderComposer(app);

    fireEvent.click(screen.getByText("Send"));
    await fail("gmail_composer.send", "request timed out");
    await fail("gmail_composer.send_status", "offline");

    expect(screen.queryByText("Message sent")).toBeNull();
    expect(screen.getByText(/timed out/)).toBeTruthy();
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
