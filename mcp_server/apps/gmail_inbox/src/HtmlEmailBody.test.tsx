import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HtmlEmailBody } from "./HtmlEmailBody";
import { BLOCKED_IMG_PLACEHOLDER } from "./remoteImages";

const REMOTE_HTML =
  '<p>newsletter</p><img src="https://cdn.example.com/logo.png" alt="logo">';

function makeApp(
  impl?: (args: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>,
) {
  const callServerTool = vi.fn(
    impl ??
      (async (args: { name: string; arguments: Record<string, unknown> }) => ({
        structuredContent: {
          url: args.arguments.url,
          mime_type: "image/png",
          data_base64: "AAAA",
        },
      })),
  );
  return { callServerTool };
}

function renderBody(app: ReturnType<typeof makeApp>, html = REMOTE_HTML) {
  return render(
    <HtmlEmailBody html={html} mcpApp={app} htmlStyle={{}} quoteToggleStyle={{}} />,
  );
}

describe("HtmlEmailBody", () => {
  it("renders blocked remote images with a banner; no fetch until asked", () => {
    const app = makeApp();
    const { container } = renderBody(app);
    expect(screen.getByTestId("show-images-banner").textContent).toContain(
      "Remote images are hidden (1)",
    );
    expect(container.innerHTML).toContain(BLOCKED_IMG_PLACEHOLDER);
    expect(container.innerHTML).not.toContain("cdn.example.com");
    expect(app.callServerTool).not.toHaveBeenCalled();
  });

  it("shows no banner when the HTML has no remote references", () => {
    const app = makeApp();
    renderBody(app, '<p>plain</p><img src="cid:x"><img src="data:image/gif;base64,R0lGOD">');
    expect(screen.queryByTestId("show-images-banner")).toBeNull();
  });

  it("Show images fetches via gmail_inbox.fetch_image and swaps in data: URIs", async () => {
    const app = makeApp();
    const { container } = renderBody(app);
    fireEvent.click(screen.getByTestId("show-images-btn"));
    await waitFor(() =>
      expect(container.innerHTML).toContain("data:image/png;base64,AAAA"),
    );
    expect(app.callServerTool).toHaveBeenCalledWith({
      name: "gmail_inbox.fetch_image",
      arguments: { url: "https://cdn.example.com/logo.png" },
    });
    expect(screen.queryByTestId("show-images-banner")).toBeNull();
  });

  it("keeps the banner with a Retry after a failed fetch, and retry can succeed", async () => {
    let fail = true;
    const app = makeApp(async (args) => {
      if (fail) throw new Error("fetch failed");
      return {
        structuredContent: {
          url: args.arguments.url,
          mime_type: "image/png",
          data_base64: "BBBB",
        },
      };
    });
    const { container } = renderBody(app);
    fireEvent.click(screen.getByTestId("show-images-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("show-images-btn").textContent).toBe("Retry"),
    );
    expect(container.innerHTML).toContain(BLOCKED_IMG_PLACEHOLDER);

    fail = false;
    fireEvent.click(screen.getByTestId("show-images-btn"));
    await waitFor(() =>
      expect(container.innerHTML).toContain("data:image/png;base64,BBBB"),
    );
    expect(screen.queryByTestId("show-images-banner")).toBeNull();
  });

  it("drops in-flight results when the html prop changes (no stale resurrection)", async () => {
    let release: (v: unknown) => void = () => {};
    const gate = new Promise((r) => (release = r));
    const app = makeApp(async (args) => {
      await gate;
      return {
        structuredContent: {
          url: args.arguments.url,
          mime_type: "image/png",
          data_base64: "STALE",
        },
      };
    });
    const { container, rerender } = renderBody(app);
    fireEvent.click(screen.getByTestId("show-images-btn"));

    // Lean -> full thread upgrade swaps the html under the same instance.
    const newHtml = '<p>full</p><img src="https://cdn.example.com/other.png">';
    rerender(
      <HtmlEmailBody html={newHtml} mcpApp={app} htmlStyle={{}} quoteToggleStyle={{}} />,
    );
    release(null);
    // The stale fetch resolves but must not hide the new banner or paint.
    await waitFor(() => expect(app.callServerTool).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container.innerHTML).not.toContain("STALE");
    expect(screen.getByTestId("show-images-banner").textContent).toContain(
      "Remote images are hidden (1)",
    );
    expect(screen.getByTestId("show-images-btn").textContent).toBe("Show images");
  });
});
