// Assemble a standalone, fixture-driven preview HTML for an MCP App.
//
//   APP=gmail_inbox bun run build.mjs
//
// Bundles the host bridge (src/host.ts), inlines the committed app bundle
// (mcp_server/apps/<APP>/dist/mcp-app.html) as base64, and writes a single
// self-contained file to dist/<APP>-preview.html. Open it in any browser -
// no server, no Gmail, no OAuth, no network.
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const APP = process.env.APP || process.argv[2] || "gmail_inbox";

const appHtmlPath = join(REPO, "mcp_server", "apps", APP, "dist", "mcp-app.html");
if (!existsSync(appHtmlPath)) {
  console.error(
    `✗ No built bundle for app "${APP}" at ${appHtmlPath}\n` +
      `  Build it first:  make build_apps   (or cd into the app and \`bun run build\`)`,
  );
  process.exit(1);
}

// Bundle the host bridge to a single ESM blob.
const built = await Bun.build({
  entrypoints: [join(HERE, "src", "host.ts")],
  format: "esm",
  minify: false,
  target: "browser",
});
if (!built.success) {
  console.error("✗ host bundle failed:");
  for (const m of built.logs) console.error(m);
  process.exit(1);
}
const hostJs = await built.outputs[0].text();
const appB64 = readFileSync(appHtmlPath).toString("base64");

// Inline widget width. Real hosts render the app inline in the conversation at
// a constrained width; override with WIDTH=... to match a specific client.
const WIDTH = Number(process.env.WIDTH) || 760;

// Optional client chrome around the widget. Off by default - the bare surface is
// what you want while iterating on an app. `CHAT=claude` wraps the app in a
// Claude.ai conversation so a screenshot shows what the app actually is: a card
// rendered inline in the host chat, not a standalone web page. Mirrors the
// landing page's ClaudeShell (landing-page/src/components/chat/ClaudeShell.astro)
// so the README and the site tell the same story.
const CHAT = process.env.CHAT || "";

// Height cap for the auto-resized iframe, read by host.ts. Anything that is not
// a finite positive number (unset, junk, zero, negative) means "uncapped" - a
// negative cap would otherwise reach Math.min() and produce a negative CSS
// height, which browsers drop on the floor.
const maxHeightNum = Number(process.env.MAXH);
const maxHeightAttr = Number.isFinite(maxHeightNum) && maxHeightNum > 0 ? maxHeightNum : 0;

// Per-app conversation copy: the prompt that would summon the app, and the tool
// the server would run to serve it. Tool names are the real registry names
// (landing-page/src/config/tool-surface.generated.json), so the chrome can't
// quietly advertise a tool this server does not expose.
const CONVO = {
  gmail_inbox: {
    ask: "Triage my inbox and draft replies.",
    tool: "gmail_curate_inbox",
    say: "I triaged your inbox. Here's the curated view.",
  },
  gmail_composer: {
    ask: "Reply to Priya about the onsite loop.",
    tool: "gmail_reply_to_thread",
    say: "Drafted a reply on that thread - review and send it below.",
  },
  pdf_signer: {
    ask: "Get the NDA ready for me to sign.",
    tool: "pdf_request_signature",
    say: "The NDA is locked for signing. Type your name to sign it.",
  },
  settings: {
    ask: "Show my connected account and webhooks.",
    tool: "webhook_settings",
    say: "Here's your Gmail connection and webhook endpoints.",
  },
};

// Claude's coral sunburst, inlined so the preview stays a single self-contained
// file (same asset as landing-page/public/logos/claude.svg).
const CLAUDE_MARK = `<svg viewBox="0 0 256 257" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path fill="#D97757" d="m50.228 170.321 50.357-28.257.843-2.463-.843-1.361h-2.462l-8.426-.518-28.775-.778-24.952-1.037-24.175-1.296-6.092-1.297L0 125.796l.583-3.759 5.12-3.434 7.324.648 16.202 1.101 24.304 1.685 17.629 1.037 26.118 2.722h4.148l.583-1.685-1.426-1.037-1.101-1.037-25.147-17.045-27.22-18.017-14.258-10.37-7.713-5.25-3.888-4.925-1.685-10.758 7-7.713 9.397.649 2.398.648 9.527 7.323 20.35 15.75L94.817 91.9l3.889 3.24 1.555-1.102.195-.777-1.75-2.917-14.453-26.118-15.425-26.572-6.87-11.018-1.814-6.61c-.648-2.723-1.102-4.991-1.102-7.778l7.972-10.823L71.42 0 82.05 1.426l4.472 3.888 6.61 15.101 10.694 23.786 16.591 32.34 4.861 9.592 2.592 8.879.973 2.722h1.685v-1.556l1.36-18.211 2.528-22.36 2.463-28.776.843-8.1 4.018-9.722 7.971-5.25 6.222 2.981 5.12 7.324-.713 4.73-3.046 19.768-5.962 30.98-3.889 20.739h2.268l2.593-2.593 10.499-13.934 17.628-22.036 7.778-8.749 9.073-9.657 5.833-4.601h11.018l8.1 12.055-3.628 12.443-11.342 14.388-9.398 12.184-13.48 18.147-8.426 14.518.778 1.166 2.01-.194 30.46-6.481 16.462-2.982 19.637-3.37 8.88 4.148.971 4.213-3.5 8.62-20.998 5.184-24.628 4.926-36.682 8.685-.454.324.519.648 16.526 1.555 7.065.389h17.304l32.21 2.398 8.426 5.574 5.055 6.805-.843 5.184-12.962 6.611-17.498-4.148-40.83-9.721-14-3.5h-1.944v1.167l11.666 11.406 21.387 19.314 26.767 24.887 1.36 6.157-3.434 4.86-3.63-.518-23.526-17.693-9.073-7.972-20.545-17.304h-1.36v1.814l4.73 6.935 25.017 37.59 1.296 11.536-1.814 3.76-6.481 2.268-7.13-1.297-14.647-20.544-15.1-23.138-12.185-20.739-1.49.843-7.194 77.448-3.37 3.953-7.778 2.981-6.48-4.925-3.436-7.972 3.435-15.749 4.148-20.544 3.37-16.333 3.046-20.285 1.815-6.74-.13-.454-1.49.194-15.295 20.999-23.267 31.433-18.406 19.702-4.407 1.75-7.648-3.954.713-7.064 4.277-6.286 25.47-32.405 15.36-20.092 9.917-11.6-.065-1.686h-.583L44.07 198.125l-12.055 1.555-5.185-4.86.648-7.972 2.463-2.593 20.35-13.999-.064.065Z"/></svg>`;

// Which conversation to frame the app in. Defaults to the app being previewed,
// but the two come apart for the composer: that view is served by the gmail_inbox
// bundle, so its shot is APP=gmail_inbox SCENARIO=gmail_composer.
const SCENARIO = process.env.SCENARIO || APP;

// Everything interpolated as text into the generated HTML goes through this.
// APP and SCENARIO are operator-supplied, so this is hygiene rather than a trust
// boundary - but the output is a single file meant to be opened in a browser and
// handed around, and a template literal is not an HTML escaper.
const esc = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const raw = CONVO[SCENARIO] || {
  ask: `Show me ${SCENARIO}.`,
  tool: SCENARIO,
  say: "Here you go.",
};
const convo = { ask: esc(raw.ask), tool: esc(raw.tool), say: esc(raw.say) };

const WIDGET = `<div class="widget"><iframe id="app" title="${esc(APP)} MCP App"></iframe></div>`;

// The app card sits where a tool result lands: under the assistant's line and
// its tool-call chip, inside the assistant turn. Same order as ClaudeShell.
const chatFrame = `<div class="chat">
      <div class="chat-hd">
        <span class="pill">${CLAUDE_MARK}Claude
          <svg class="caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
        </span>
      </div>
      <div class="chat-body">
        <div class="turn-u"><p>${convo.ask}</p></div>
        <div class="turn-a">
          <span class="av">${CLAUDE_MARK}</span>
          <div class="a-col">
            <p class="serif">${convo.say}</p>
            <div class="chip">
              <svg class="chip-i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round"><path d="M7 8l4 4-4 4m6 0h6"/></svg>
              ${convo.tool}<b>&#10003;</b>
            </div>
            ${WIDGET}
          </div>
        </div>
      </div>
      <div class="chat-ft">
        <div class="composer">Reply to Claude&#8230;
          <span class="send"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
            stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></span>
        </div>
      </div>
    </div>`;

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MCP-UI preview · ${esc(APP)}</title>
<style>
  /* Neutral host surface only - no chrome. The framed widget is the app exactly
     as a real MCP host embeds it inline; a real host renders it on its own
     background with rounded corners and fits the height to the content. */
  :root { --surface:#ffffff; --ground:#f0f1f3; --hair:rgba(0,0,0,.10); color-scheme: light; }
  @media (prefers-color-scheme: dark) {
    :root { --surface:#ffffff; --ground:#0f1012; --hair:rgba(255,255,255,.10); color-scheme: dark; }
  }
  :root[data-theme="light"] { --ground:#f0f1f3; --hair:rgba(0,0,0,.10); color-scheme: light; }
  :root[data-theme="dark"] { --ground:#0f1012; --hair:rgba(255,255,255,.10); color-scheme: dark; }
  html, body { margin:0; background:var(--ground); }
  .stage { min-height:100vh; padding:24px 16px; display:flex; justify-content:center;
    align-items:flex-start; box-sizing:border-box; }
  /* The widget frame: white surface, rounded, hairline + soft shadow - the
     framing hosts apply. The app light UI keeps a light surface in both themes. */
  .col { display:flex; flex-direction:column; gap:14px; width:${WIDTH}px; max-width:100%; }
  .widget { width:100%; background:var(--surface);
    border:1px solid var(--hair); border-radius:16px; overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.12); }
  iframe { width:100%; height:520px; border:0; display:block; background:var(--surface); }
  /* Host-side log of app-initiated ui/update-model-context pushes: a real host
     appends these to the LLM's context invisibly, the preview makes them
     visible so send/discard flows can be smoke tested end to end. */
  .ctx { background:var(--surface); border:1px dashed var(--hair);
    border-radius:12px; padding:12px 14px; }
  .ctx-head { font:600 12px -apple-system, BlinkMacSystemFont, sans-serif;
    color:#334155; margin-bottom:8px; }
  .ctx-sub { font-weight:400; color:#94a3b8; }
  .ctx pre { white-space:pre-wrap; overflow-wrap:anywhere;
    font:11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    background:#f8fafc; border:1px solid var(--hair); border-radius:8px;
    padding:10px; margin:0 0 8px; color:#0f172a; }
  #err { color:#c0392b; white-space:pre-wrap; font:12px ui-monospace, monospace;
    max-width:${WIDTH}px; margin:8px auto 0; }
  /* Claude.ai chat chrome (CHAT=claude only). Cream paper canvas, serif
     assistant copy, coral sunburst + send button - approximating Claude's
     published brand coral (#D97757/#c96442) and surfaces, same as the landing
     page shell. Inert markup: nothing here talks to the app. */
  .chat { border:1px solid #e5e0d6; border-radius:12px; background:#f5f1e8;
    color:#2d2a26; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.12); }
  .chat-hd { border-bottom:1px solid #e9e3d6; padding:10px 16px; }
  .pill { display:inline-flex; align-items:center; gap:6px; border:1px solid #e0d9c9;
    background:#faf8f2; border-radius:999px; padding:4px 10px; color:#5c574d;
    font:500 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .pill > svg:first-child { width:14px; height:14px; }
  .caret { width:12px; height:12px; color:#9a9286; }
  .chat-body { padding:16px; }
  .turn-u { display:flex; justify-content:flex-end; margin-bottom:16px; }
  .turn-u p { margin:0; max-width:80%; background:#e7e1d4; border-radius:16px;
    padding:8px 16px; font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .turn-a { display:flex; gap:12px; }
  .av { flex:0 0 auto; width:24px; height:24px; margin-top:2px; }
  .av svg { width:24px; height:24px; display:block; }
  .a-col { flex:1; min-width:0; }
  .serif { margin:0 0 10px; font:15px/1.6 Georgia, "Times New Roman", serif; color:#2d2a26; }
  .chip { display:inline-flex; align-items:center; gap:6px; border:1px solid #e0d9c9;
    background:#faf8f2; border-radius:8px; padding:4px 8px; margin:0 0 10px;
    font:11px ui-monospace, SFMono-Regular, Menlo, monospace; color:#7a756b; }
  .chip-i { width:12px; height:12px; color:#c96442; }
  .chip b { color:#c96442; font-weight:400; }
  .chat-ft { padding:0 16px 16px; }
  .composer { display:flex; align-items:center; justify-content:space-between; gap:8px;
    border:1px solid #e0d9c9; background:#fff; border-radius:16px; padding:10px 16px;
    color:#9a9286; font:14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .send { display:grid; place-items:center; flex:0 0 auto; width:28px; height:28px;
    border-radius:999px; background:#c96442; color:#fff; }
  .send svg { width:16px; height:16px; }
  /* Inside the chat the card is a tool result on cream, not a floating widget:
     keep the hairline, drop the page-level drop shadow. */
  .chat .widget { border-radius:12px; box-shadow:none; }
</style>
</head>
<body>
<div class="stage">
  <div class="col">
    ${CHAT === "claude" ? chatFrame : WIDGET}
    <div id="ctx" class="ctx" style="display:none">
      <div class="ctx-head">Model context updates
        <span class="ctx-sub">- what the app pushed to the LLM via ui/update-model-context</span>
      </div>
      <div id="ctx-items"></div>
    </div>
  </div>
</div>
<div id="err"></div>
<script>
  window.__APP_NAME__ = ${JSON.stringify(APP)};
  window.__MAX_HEIGHT__ = ${maxHeightAttr};
  window.__APP_HTML_B64__ = "${appB64}";
</script>
<script type="module">
${hostJs}
</script>
</body>
</html>
`;

const outDir = join(HERE, "dist");
mkdirSync(outDir, { recursive: true });
const outPath = join(outDir, `${APP}-preview.html`);
await Bun.write(outPath, page);
console.log(
  `✓ ${APP} preview → ${outPath} (${(page.length / 1_000_000).toFixed(2)} MB)`,
);
console.log(`  Open it in a browser, or run:  make preview_smoke APP=${APP}`);
