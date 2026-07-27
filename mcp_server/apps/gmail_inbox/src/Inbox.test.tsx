import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Inbox,
  type CurateResult,
  type CuratedThread,
  type Thread,
} from "./Inbox";
import { bufferToolResults } from "./helpers";

function makeMcpApp(opts?: {
  threadResults?: Record<string, Thread>;
  refreshResult?: CurateResult;
}) {
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const callServerTool = vi.fn(
    async (args: { name: string; arguments: Record<string, unknown> }) => {
      calls.push(args);
      if (args.name === "gmail_inbox.open_thread") {
        const tid = args.arguments.thread_id as string;
        const t = opts?.threadResults?.[tid];
        return t ? { structuredContent: t } : null;
      }
      if (args.name === "gmail_inbox.refresh") {
        return opts?.refreshResult
          ? { structuredContent: opts.refreshResult }
          : { structuredContent: { threads: [] } };
      }
      return null;
    }
  );
  const openLink = vi.fn(async () => ({}));
  const app: {
    ontoolresult?: (raw: unknown) => void;
    callServerTool: typeof callServerTool;
    openLink: typeof openLink;
  } = { callServerTool, openLink };
  return { app, callServerTool, openLink, calls };
}

const threadA: CuratedThread = {
  thread_id: "tA",
  subject: "VIP message",
  from: "ceo@example.com",
  snippet: "urgent...",
  last_message_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
  importance_score: 0.85,
  reasons: ["Marked IMPORTANT by Gmail", "Unread"],
};

const threadB: CuratedThread = {
  thread_id: "tB",
  subject: "Just a hello",
  from: "friend@example.com",
  snippet: "hey",
  last_message_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
  importance_score: 0.32,
  reasons: ["Recent (~72h old)"],
};

const sampleResult: CurateResult = { threads: [threadA, threadB] };

const plainThread: Thread = {
  thread_id: "tA",
  messages: [
    {
      message_id: "m1",
      from: "ceo@example.com",
      to: "me@example.com",
      date: new Date().toISOString(),
      subject: "VIP message",
      body_text: "Hello world plain body",
      attachments: [
        {
          filename: "doc.pdf",
          mime_type: "application/pdf",
          size: 1024,
          attachment_id: "att-1",
        },
      ],
    },
  ],
};

const htmlThread: Thread = {
  thread_id: "tB",
  messages: [
    {
      message_id: "m2",
      from: "friend@example.com",
      to: "me@example.com",
      date: new Date().toISOString(),
      subject: "Just a hello",
      body_html: "<p data-testid='html-body'>html body here</p>",
      attachments: [],
    },
  ],
};

describe("Inbox", () => {
  it("renders empty state before any tool result", () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    expect(screen.getByText(/loading inbox/i)).toBeInTheDocument();
    expect(screen.getByText(/select a thread/i)).toBeInTheDocument();
  });

  it("renders curated threads once ontoolresult fires", async () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    expect(await screen.findByText("VIP message")).toBeInTheDocument();
    expect(screen.getByText("Just a hello")).toBeInTheDocument();
  });

  it("renders banked ledger verdicts + coverage from inbox_get_curation", async () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({
      structuredContent: {
        records: [
          {
            thread_id: "tL",
            bucket: "needs_reply",
            importance: 0.9,
            summary: "Investor wants the updated deck by Friday",
            suggested_action: "reply",
            ledger_status: "curated",
          },
          {
            thread_id: "tS",
            bucket: "fyi",
            importance: 0.4,
            summary: "Weekly digest",
            ledger_status: "stale",
          },
        ],
        coverage: { curated: 1, stale: 1, uncurated: 3 },
      },
    });
    // Summary becomes the row's primary line; the ledger row is clickable.
    expect(
      await screen.findByText(/Investor wants the updated deck/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("row-tL")).toBeInTheDocument();
    // Coverage banner surfaces the curated / stale / uncurated counts.
    const banner = screen.getByTestId("coverage-banner");
    expect(banner).toHaveTextContent("1 triaged");
    expect(banner).toHaveTextContent("1 stale");
    expect(banner).toHaveTextContent("3 not yet triaged");
  });

  it("clicking a row calls gmail_inbox.open_thread and renders the thread", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    fireEvent.click(row);
    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.name === "gmail_inbox.open_thread" &&
            c.arguments.thread_id === "tA"
        )
      ).toBe(true);
    });
    expect(await screen.findByTestId("msg-m1")).toBeInTheDocument();
    expect(screen.getByText(/Hello world plain body/)).toBeInTheDocument();
  });

  it("renders the thread reader from a result buffered before mount (no inbox fallback)", async () => {
    const { app, calls } = makeMcpApp({ threadResults: { tA: plainThread } });
    const buffer = bufferToolResults(app);
    // Host delivers the thread result BEFORE <Inbox> mounts (the race).
    app.ontoolresult?.({ structuredContent: plainThread });
    render(<Inbox mcpApp={app} resultBuffer={buffer} />);
    // The buffered result is drained on mount -> reader renders the thread, not
    // the curated inbox.
    expect(await screen.findByTestId("msg-m1")).toBeInTheDocument();
    expect(screen.getByText(/Hello world plain body/)).toBeInTheDocument();
    // The 800ms curated-inbox fallback must NOT fire, since the result arrived.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(
      calls.some((c) => c.name === "gmail_inbox.refresh")
    ).toBe(false);
  });

  it("renders HTML body when no plaintext is provided", async () => {
    const { app } = makeMcpApp({
      threadResults: { tB: htmlThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tB");
    fireEvent.click(row);
    expect(await screen.findByTestId("html-body")).toBeInTheDocument();
  });

  it("renders attachment chips in the message", async () => {
    const { app } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    const chips = await screen.findAllByTestId("attachment-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent(/doc\.pdf/);
  });

  it("mark-read optimistically de-bolds the row by clearing the unread chip", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    fireEvent.click(row);
    await screen.findByTestId("msg-m1");
    const subjectInRow = row.querySelector("span") as HTMLElement;
    expect(subjectInRow).toHaveStyle({ fontWeight: "700" });
    const markBtn = screen.getByRole("button", { name: /mark read/i });
    fireEvent.click(markBtn);
    await waitFor(() => {
      expect(
        calls.some((c) => c.name === "gmail_inbox.mark_read")
      ).toBe(true);
    });
    await waitFor(() => {
      const updatedRow = screen.getByTestId("row-tA");
      const span = updatedRow.querySelector("span") as HTMLElement;
      expect(span).toHaveStyle({ fontWeight: "500" });
    });
  });

  it("archive removes the row from the list optimistically", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    await screen.findByTestId("msg-m1");
    fireEvent.click(screen.getByRole("button", { name: /archive/i }));
    await waitFor(() => {
      expect(screen.queryByTestId("row-tA")).not.toBeInTheDocument();
    });
    expect(calls.some((c) => c.name === "gmail_inbox.archive")).toBe(true);
    expect(screen.getByText(/select a thread/i)).toBeInTheDocument();
  });

  it("reply triggers gmail_inbox.reply and shows a transient status", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    await screen.findByTestId("msg-m1");
    const replyBtns = screen.getAllByRole("button", { name: /reply/i });
    fireEvent.click(replyBtns[0]);
    await waitFor(() => {
      expect(calls.some((c) => c.name === "gmail_inbox.reply")).toBe(true);
    });
    expect(
      await screen.findByText(/reply draft created/i)
    ).toBeInTheDocument();
  });

  it("mark-done calls gmail_inbox.mark_done and removes the row", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    await screen.findByTestId("msg-m1");
    const doneBtns = screen.getAllByRole("button", { name: /mark done/i });
    const actionBarBtn = doneBtns.find(
      (b) => !b.closest('[data-testid^="row-"]')
    )!;
    fireEvent.click(actionBarBtn);
    await waitFor(() => {
      expect(screen.queryByTestId("row-tA")).not.toBeInTheDocument();
    });
    expect(
      calls.some((c) => c.name === "gmail_inbox.mark_done")
    ).toBe(true);
    expect(screen.getByText(/select a thread/i)).toBeInTheDocument();
  });

  it("mark-done from sidebar row button removes without opening", async () => {
    const { app, calls } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    const doneBtn = row.querySelector('button[title="Mark done"]') as HTMLElement;
    fireEvent.click(doneBtn);
    await waitFor(() => {
      expect(screen.queryByTestId("row-tA")).not.toBeInTheDocument();
    });
    expect(
      calls.some((c) => c.name === "gmail_inbox.mark_done" && c.arguments.thread_id === "tA")
    ).toBe(true);
  });

  it("clears the ontoolresult handler on unmount", () => {
    const { app } = makeMcpApp();
    const { unmount } = render(<Inbox mcpApp={app} />);
    expect(app.ontoolresult).toBeDefined();
    unmount();
    expect(app.ontoolresult).toBeUndefined();
  });

  // Mobile MCP hosts embed the app in a sandboxed iframe inside the chat's own
  // scroll view, where touch-dragging a nested `overflow: auto` pane is
  // unreliable. On narrow viewports the list/reader must flow at their natural
  // height (so the host page scrolls the iframe) instead of trapping scroll.
  it("narrow viewport lets the list flow instead of trapping scroll", async () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    try {
      const { app } = makeMcpApp();
      render(<Inbox mcpApp={app} />);
      app.ontoolresult?.({ structuredContent: sampleResult });
      const row = await screen.findByTestId("row-tA");
      const list = row.closest("ul")!;
      expect(list.style.overflowY).toBe("visible");
    } finally {
      window.matchMedia = original;
    }
  });

  it("wide viewport keeps the list as its own scroll region", async () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    const list = row.closest("ul")!;
    expect(list.style.overflowY).toBe("auto");
  });
});
