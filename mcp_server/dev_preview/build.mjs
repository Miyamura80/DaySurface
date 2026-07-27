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

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MCP-UI preview · ${APP}</title>
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
</style>
</head>
<body>
<div class="stage">
  <div class="col">
    <div class="widget"><iframe id="app" title="${APP} MCP App"></iframe></div>
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
