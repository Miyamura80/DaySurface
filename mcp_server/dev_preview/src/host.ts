// Fixture host for MCP-UI apps.
//
// Plays the *host* side of the ext-apps postMessage protocol (the counterpart
// to the `App` client each app bundle runs) using a no-client `AppBridge`, and
// answers every `callServerTool` from local fixtures. This renders a committed
// `dist/mcp-app.html` bundle with zero backing services - no MCP server, no
// Gmail, no OAuth, no network.
//
// Two values are injected by build.mjs into the generated HTML and are always
// present (the generated file is the only entry point):
//   window.__APP_NAME__      e.g. "gmail_inbox" (selects the initial payload)
//   window.__APP_HTML_B64__  the base64-encoded app bundle
import {
  AppBridge,
  PostMessageTransport,
} from "@modelcontextprotocol/ext-apps/app-bridge";
import { dispatch, initialResult } from "./fixtures";

const IMPLEMENTATION = { name: "mcp-ui-fixture-host", version: "0.1.0" };

type Globals = {
  __APP_NAME__?: string;
  __APP_HTML_B64__?: string;
  __READY__?: boolean;
  __MODEL_CONTEXT__?: string[];
};

type ModelContextParams = { content?: { type?: string; text?: string }[] };

// Render an app-initiated `ui/update-model-context` push into the host page's
// context panel (and onto window.__MODEL_CONTEXT__ so headless smoke runs can
// assert on it). A real host would append this to the LLM's context; showing
// it is the whole point of previewing the send/discard flows.
function renderModelContext(params: ModelContextParams): void {
  const texts = (params.content ?? [])
    .filter((c) => c.type === "text" && typeof c.text === "string")
    .map((c) => c.text as string);
  if (texts.length === 0) return;
  const g = window as unknown as Globals;
  (g.__MODEL_CONTEXT__ ??= []).push(...texts);
  const panel = document.getElementById("ctx");
  const list = document.getElementById("ctx-items");
  if (!panel || !list) return;
  panel.style.display = "block";
  for (const text of texts) {
    const pre = document.createElement("pre");
    pre.textContent = text;
    list.appendChild(pre);
  }
}

function requireGlobal(key: "__APP_NAME__" | "__APP_HTML_B64__"): string {
  const value = (window as unknown as Globals)[key];
  if (!value) {
    throw new Error(
      `[dev_preview] missing window.${key}; regenerate with \`make preview_app\``,
    );
  }
  return value;
}

function decodeAppHtml(b64: string): string {
  return new TextDecoder().decode(
    Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)),
  );
}

async function main(): Promise<void> {
  const appName = requireGlobal("__APP_NAME__");
  const iframe = document.getElementById("app") as HTMLIFrameElement;
  const win = iframe.contentWindow!;
  const width = Math.round(iframe.getBoundingClientRect().width) || 760;

  const bridge = new AppBridge(
    null,
    IMPLEMENTATION,
    { openLinks: {}, updateModelContext: { text: {} } },
    {
      hostContext: {
        theme: "light",
        platform: "web",
        containerDimensions: { width, maxHeight: 6000 },
        displayMode: "inline",
        availableDisplayModes: ["inline", "fullscreen"],
      },
    },
  );

  bridge.oncalltool = async ({ name, arguments: args }) =>
    dispatch(name, (args ?? {}) as Record<string, unknown>);
  bridge.onopenlink = async () => ({});
  bridge.onmessage = async () => ({});
  bridge.onupdatemodelcontext = async (params) => {
    renderModelContext(params as ModelContextParams);
    return {};
  };
  bridge.onrequestdisplaymode = async ({ mode }) => ({ mode });

  // Fit the frame to the app's content the way a real host does: the app emits
  // ui/notifications/size-changed (autoResize) with its measured height, and we
  // size the iframe to it instead of leaving a fixed box with dead whitespace.
  bridge.onsizechange = ({ height }) => {
    if (height && height > 0) iframe.style.height = `${Math.ceil(height)}px`;
  };

  bridge.oninitialized = async () => {
    // ext-apps contract: sendToolInput must precede sendToolResult.
    await bridge.sendToolInput({ arguments: {} });
    const result = initialResult(appName);
    // Some apps register their `ontoolresult` handler only after React mounts
    // (i.e. after connect() resolves), so a result sent the instant the app
    // reports initialized can be missed - the inbox self-heals via its own
    // fallback fetch, the composer has none. Re-send over the first ~700ms to
    // cover late registration. Resending the same payload is idempotent, and
    // the window closes before the user could have edited anything.
    await bridge.sendToolResult(result);
    for (const delay of [200, 600]) {
      setTimeout(() => bridge.sendToolResult(result).catch(() => {}), delay);
    }
    (window as unknown as Globals).__READY__ = true;
  };

  // Attach the host transport BEFORE the app document runs so the app's
  // `ui/initialize` request is never missed. Writing into the about:blank
  // iframe keeps `contentWindow` stable (no navigation → event.source matches),
  // which is what makes a single-iframe host work without the upstream
  // double-iframe sandbox relay.
  await bridge.connect(new PostMessageTransport(win, win));

  const html = decodeAppHtml(requireGlobal("__APP_HTML_B64__"));
  const doc = iframe.contentDocument!;
  doc.open();
  doc.write(html);
  doc.close();
}

main().catch((e: unknown) => {
  const el = document.getElementById("err");
  const err = e as { stack?: string } | undefined;
  if (el) el.textContent = "Host error: " + (err?.stack ?? String(e));
});
