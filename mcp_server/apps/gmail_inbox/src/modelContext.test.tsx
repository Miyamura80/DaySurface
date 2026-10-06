import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { InlineComposer } from "./InlineComposer";
import { reportComposerAction } from "./modelContext";
import type { ComposerDraft, McpAppLike } from "./types";

const draft: ComposerDraft = {
  draft_id: "d1",
  to: "margaret@example.com",
  subject: "Re: NDA",
  body: "Hi Margaret,",
  thread_id: "t1",
};

function makeApp(caps: { updateModelContext?: unknown } | undefined = { updateModelContext: {} }) {
  const updateModelContext = vi.fn(async () => ({}));
  const app: McpAppLike = {
    callServerTool: vi.fn(async (args: { name: string }) => {
      if (args.name === "gmail_composer.send") {
        return { structuredContent: { message_id: "m123", thread_id: "t1" } };
      }
      if (args.name === "gmail_composer.discard") {
        return { structuredContent: { discarded: true } };
      }
      return {};
    }),
    openLink: vi.fn(async () => ({})),
    updateModelContext,
    getHostCapabilities: () => caps,
  };
  return { app, updateModelContext };
}

function lastText(fn: ReturnType<typeof vi.fn>): string {
  const params = fn.mock.calls.at(-1)?.[0] as { content: { text: string }[] };
  return params.content[0].text;
}

function renderComposer(app: McpAppLike) {
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

describe("composer actions reach the model", () => {
  // A confirmed send schedules onSent after 1.5s; keep that timer fake.
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("tells the model a draft was sent", async () => {
    const { app, updateModelContext } = makeApp();
    renderComposer(app);

    await act(async () => { fireEvent.click(screen.getByText("Send")); });

    expect(updateModelContext).toHaveBeenCalledTimes(1);
    const text = lastText(updateModelContext);
    expect(text).toContain("SENT draft d1 as message m123 on thread t1");
    expect(text).toContain('subject "Re: NDA", to margaret@example.com');
    expect(text).toContain("Do not edit, resend, or recreate them");
  });

  it("tells the model a draft was discarded", async () => {
    const { app, updateModelContext } = makeApp();
    renderComposer(app);

    await act(async () => { fireEvent.click(screen.getByTitle("Discard draft")); });

    expect(lastText(updateModelContext)).toContain("DISCARDED draft d1 on thread t1");
  });

  it("says nothing when the discard failed", async () => {
    const { app, updateModelContext } = makeApp();
    app.callServerTool = vi.fn(async () => ({
      isError: true,
      content: [{ type: "text", text: "Gmail 500" }],
    }));
    renderComposer(app);

    await act(async () => { fireEvent.click(screen.getByTitle("Discard draft")); });

    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("names the thread Gmail put a new draft on", async () => {
    const { app, updateModelContext } = makeApp();
    await reportComposerAction(
      app,
      "sent",
      { ...draft, thread_id: undefined },
      { message_id: "m9", thread_id: "t-new" },
    );
    expect(lastText(updateModelContext)).toContain("message m9 on thread t-new");
  });

  it("says nothing when the send was not confirmed", async () => {
    const { app, updateModelContext } = makeApp();
    app.callServerTool = vi.fn(async () => ({ isError: true }));
    renderComposer(app);

    await act(async () => { fireEvent.click(screen.getByText("Send")); });

    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("skips hosts that don't accept context updates", async () => {
    const { app, updateModelContext } = makeApp({});
    await reportComposerAction(app, "sent", draft, { message_id: "m1" });
    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("re-sends recent actions, since each update replaces the last", async () => {
    const { app, updateModelContext } = makeApp();
    for (let i = 1; i <= 7; i++) {
      await reportComposerAction(app, "sent", { ...draft, draft_id: `d${i}` }, { message_id: `m${i}` });
    }
    const text = lastText(updateModelContext);
    // The newest five, oldest first.
    expect(text).not.toContain("draft d2 ");
    expect(text.indexOf("draft d3 ")).toBeLessThan(text.indexOf("draft d7 "));
  });

  it("swallows a host that throws on the capability check", async () => {
    const { app, updateModelContext } = makeApp();
    app.getHostCapabilities = () => { throw new Error("host bug"); };
    await expect(reportComposerAction(app, "sent", draft, { message_id: "m1" })).resolves.toBeUndefined();
    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("swallows a rejected update", async () => {
    const { app, updateModelContext } = makeApp();
    updateModelContext.mockRejectedValueOnce(new Error("unsupported"));
    await expect(reportComposerAction(app, "sent", draft, { message_id: "m1" })).resolves.toBeUndefined();
  });
});
