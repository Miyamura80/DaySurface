---
name: local-sandbox
description: Preview an MCP-UI app (mcp_server/apps/*) from fixtures in a browser with no MCP server, Gmail, OAuth, or network. Use when asked to see, screenshot, or test how an MCP App iframe renders, or to render it live in the Claude Code cloud.
---

# Local sandbox: preview MCP-UI apps from fixtures

Render a committed MCP App bundle (`mcp_server/apps/<name>/dist/mcp-app.html`)
in a browser driven by canned data, so you can see and click the UI without a
running MCP server, Gmail account, OAuth, or a tunnel. The harness lives in
`mcp_server/dev_preview/` (read its `README.md` for design details).

Use this when the task is "show me how the `<app>` app looks", "screenshot the
MCP App", "does the iframe render", or "preview it here in the cloud".

## 1. Build the preview

```bash
make preview_app APP=gmail_inbox      # -> mcp_server/dev_preview/dist/gmail_inbox-preview.html
make preview_app APP=gmail_composer
```

`APP` is any directory under `mcp_server/apps/`. The output is one
self-contained HTML file (the app bundle is inlined), so it also renders when
opened standalone. `dist/mcp-app.html` is committed, so this works out of the
box; if you changed an app's frontend, run `make build_apps` first.

Tune the inline width to match a client (a narrow width triggers the app's
single-column mobile layout):

```bash
make preview_app APP=gmail_inbox WIDTH=460
```

## 2. Confirm it actually rendered

```bash
make preview_smoke APP=gmail_inbox     # headless Playwright; asserts fixture content painted
```

A pass means the app connected, received fixtures, and painted an app-specific
marker (not just "connected but blank").

## 3. See it in the Claude Code cloud

The generated file is what you surface. Two ways, pick by what the user wants:

- **Screenshot (static, guaranteed):** drive the file with the preinstalled
  Chromium and send the PNG. Reliable and needs no CSP cooperation.
- **Artifact (live, interactive):** publish `dist/<app>-preview.html` as an
  Artifact so the user can click around in the side panel. It is fully
  self-contained (no external requests), which satisfies the Artifact CSP.

Screenshot snippet (Chromium is preinstalled under `/opt/pw-browsers` in the
sandbox; `bun run` it from `mcp_server/dev_preview/`):

```js
import { chromium } from "playwright";
import { globSync } from "node:fs";
const exe = globSync("/opt/pw-browsers/chromium-*/chrome-linux/chrome")[0];
const app = process.env.APP || "gmail_inbox";
const b = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--allow-file-access-from-files"] });
const p = await b.newPage({ viewport: { width: 900, height: 900 }, deviceScaleFactor: 2 });
await p.goto(`file://${process.cwd()}/dist/${app}-preview.html`, { waitUntil: "load" });
await p.waitForFunction(() => window.__READY__ === true, { timeout: 20000 }).catch(() => {});
await p.waitForTimeout(1500);
await p.screenshot({ path: `/tmp/${app}.png`, fullPage: true });
await b.close();
```

To interact (e.g. open a thread), find the app iframe and click a selector
before screenshotting:

```js
const frame = p.frames().find((f) => f !== p.mainFrame());
await frame.click('[data-testid="row-t-1001"]');   // gmail_inbox thread row
await p.waitForTimeout(1000);
```

## 4. Add fixtures for a new app

Edit `mcp_server/dev_preview/src/fixtures.ts`:

1. Add the app's **initial payload** to `initialResult()` - the first tool
   result pushed on load, which drives the first paint (an inbox curate list, a
   composer draft, etc.). Match the shape the app's `ontoolresult` expects (see
   `mcp_server/apps/<name>/src/*.tsx`).
2. Handle the app's tool names in `dispatch()` - each `callServerTool(name)` the
   app makes (thread opens, saves, etc.). Return `ok(data)`; unknown names fall
   through to an empty result.
3. `make preview_app APP=<name>` and screenshot.

If an app shows a stuck "Loading..." / "Waiting..." state, it likely registers
its `ontoolresult` handler after mount and missed the first result; the host
already re-sends over the first ~700ms, so first check the initial payload shape
and the tool names in `dispatch()`.

## Gotchas

- Needs `bun` and a Chromium. Both are preinstalled in the Claude Code cloud
  sandbox; locally, `bunx playwright install chromium` once.
- The harness is a JS-only island (like `mcp_server/apps/`) and is intentionally
  out of `make ci`, matching `make build_apps`.
- `dist/` is gitignored - regenerate with `make preview_app`, do not commit it.
- Do not put em dashes in any committed source; the `ai-writing` pre-commit hook
  rejects them (use " - ").
