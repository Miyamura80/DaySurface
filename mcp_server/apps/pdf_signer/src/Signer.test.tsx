// Component tests for the signing ceremony UI. pdf.js is mocked (jsdom has
// no canvas); the McpAppLike surface is a hand-rolled mock whose shape is
// pinned against the real SDK in appContract.test.ts.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  AnnotationMode: { ENABLE_FORMS: 2 },
  getDocument: () => ({
    promise: Promise.resolve({
      numPages: 0, // page rasterization is exercised in the real browser only
      getPage: vi.fn(),
    }),
  }),
}));
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "" }));

import { Signer, type McpAppLike } from "./Signer";

const DOC = {
  doc_id: "brave-doc",
  filename: "nda.pdf",
  status: "awaiting_signature",
  page_count: 1,
  stamp_page: 1,
  stamp_rect: [102, 120, 302, 156],
  data_base64: btoa("%PDF-fake"),
};

function makeApp(overrides: Partial<Record<string, unknown>> = {}) {
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const responses: Record<string, unknown> = {
    "pdf_signer.get_document": { structuredContent: DOC },
    "pdf_signer.sign": {
      structuredContent: {
        doc_id: DOC.doc_id,
        status: "signed",
        signed_by: "Eito Miyamura",
        signed_at_utc: "2026-07-05T12:00:00+00:00",
        message: "Signed by Eito Miyamura on 2026-07-05T12:00:00+00:00.",
      },
    },
    "pdf_signer.cancel": {
      structuredContent: { doc_id: DOC.doc_id, status: "open" },
    },
    ...overrides,
  };
  const app: McpAppLike & { calls: typeof calls } = {
    calls,
    callServerTool: vi.fn(async (args) => {
      calls.push(args);
      const value = responses[args.name];
      if (value instanceof Error) throw value;
      return value;
    }),
    openLink: vi.fn(async () => undefined),
    sendMessage: vi.fn(async () => undefined),
  };
  return app;
}

async function renderWithDocument(app: ReturnType<typeof makeApp>) {
  render(<Signer mcpApp={app} />);
  await act(async () => {
    app.ontoolresult?.({
      structuredContent: { doc_id: DOC.doc_id, status: "awaiting_user_signature" },
    });
  });
  await waitFor(() =>
    expect(screen.getByText("Awaiting your signature")).toBeInTheDocument()
  );
}

describe("Signer ceremony", () => {
  beforeEach(() => vi.clearAllMocks());

  it("waits for a tool result, then loads the document", async () => {
    const app = makeApp();
    render(<Signer mcpApp={app} />);
    expect(screen.getByText(/Waiting for a document/)).toBeInTheDocument();
    await act(async () => {
      app.ontoolresult?.({ structuredContent: { doc_id: DOC.doc_id } });
    });
    await waitFor(() => expect(screen.getByText("nda.pdf")).toBeInTheDocument());
    expect(app.calls[0]).toEqual({
      name: "pdf_signer.get_document",
      arguments: { doc_id: DOC.doc_id },
    });
  });

  it("keeps Sign disabled until name AND consent are provided", async () => {
    const app = makeApp();
    await renderWithDocument(app);
    const signBtn = screen.getByRole("button", { name: /Sign document/ });
    expect(signBtn).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Sign as/), {
      target: { value: "Eito Miyamura" },
    });
    expect(signBtn).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(signBtn).toBeEnabled();
    // Unticking consent disables it again.
    fireEvent.click(screen.getByRole("checkbox"));
    expect(signBtn).toBeDisabled();
  });

  it("signs with the typed name and shows the signed badge", async () => {
    const app = makeApp();
    await renderWithDocument(app);
    fireEvent.change(screen.getByLabelText(/Sign as/), {
      target: { value: "  Eito Miyamura  " },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Sign document/ }));
    await waitFor(() =>
      expect(screen.getByTestId("signed-badge")).toBeInTheDocument()
    );
    const signCall = app.calls.find((c) => c.name === "pdf_signer.sign");
    expect(signCall?.arguments).toEqual({
      doc_id: DOC.doc_id,
      typed_name: "Eito Miyamura",
      consent: true,
    });
    expect(screen.getByTestId("signed-badge").textContent).toContain(
      "Signed by Eito Miyamura"
    );
    // The ceremony inputs are gone once signed.
    expect(screen.queryByLabelText(/Sign as/)).not.toBeInTheDocument();
    // The model is nudged to continue via sendMessage (best-effort).
    expect(app.sendMessage).toHaveBeenCalled();
  });

  it("declined host confirmation keeps the ceremony open with a notice", async () => {
    const app = makeApp({
      "pdf_signer.sign": {
        structuredContent: {
          doc_id: DOC.doc_id,
          status: "declined",
          message: "Signing was not confirmed in the host dialog.",
        },
      },
    });
    await renderWithDocument(app);
    fireEvent.change(screen.getByLabelText(/Sign as/), {
      target: { value: "Eito Miyamura" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Sign document/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("not confirmed");
    expect(
      screen.getByRole("button", { name: /Sign document/ })
    ).toBeInTheDocument();
    expect(app.sendMessage).not.toHaveBeenCalled();
  });

  it("sign errors surface as a banner and allow retry", async () => {
    const app = makeApp({
      "pdf_signer.sign": new Error("boom"),
    });
    await renderWithDocument(app);
    fireEvent.change(screen.getByLabelText(/Sign as/), {
      target: { value: "Eito Miyamura" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Sign document/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("Signing failed");
    expect(screen.getByRole("button", { name: /Sign document/ })).toBeInTheDocument();
  });

  it("cancel calls pdf_signer.cancel and shows the cancelled state", async () => {
    const app = makeApp();
    await renderWithDocument(app);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.getByText("Signing cancelled")).toBeInTheDocument()
    );
    expect(app.calls.some((c) => c.name === "pdf_signer.cancel")).toBe(true);
    expect(
      screen.getByText(/can be edited again/)
    ).toBeInTheDocument();
  });

  it("an already-signed document shows the badge instead of the ceremony", async () => {
    const app = makeApp({
      "pdf_signer.get_document": {
        structuredContent: { ...DOC, status: "signed" },
      },
    });
    render(<Signer mcpApp={app} />);
    await act(async () => {
      app.ontoolresult?.({ structuredContent: { doc_id: DOC.doc_id } });
    });
    await waitFor(() =>
      expect(screen.getByTestId("signed-badge")).toBeInTheDocument()
    );
    expect(screen.queryByLabelText(/Sign as/)).not.toBeInTheDocument();
  });
});
