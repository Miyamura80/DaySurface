# MCP-UI dev preview

Render a committed MCP App bundle (`mcp_server/apps/<name>/dist/mcp-app.html`)
in a browser **with no MCP server, no Gmail, no OAuth, and no network** - so you
can see and click the UI while iterating in a sandbox.

It works by playing the *host* side of the ext-apps postMessage protocol (the
counterpart to the `App` client each bundle runs) with a no-client
`AppBridge`, answering every `callServerTool` from local fixtures.

```
  host page  ──AppBridge (no-client)──►  about:blank iframe
  (fixtures)  ◄──callServerTool─────────  <app> bundle (dist/mcp-app.html)
     │                                    injected via document.write
     └─ oncalltool        → dispatch(name, args)   (fixtures.ts)
        oninitialized      → sendToolResult(initial payload)
```

Writing the bundle into an `about:blank` iframe (instead of `srcdoc`/`src`)
keeps `contentWindow` stable, so the host listener is attached before the app
fires `ui/initialize` - which is what lets a single iframe work without the
upstream double-iframe sandbox relay.

## Use

```bash
# Build a standalone preview file (needs the app already built; see below)
make preview_app APP=gmail_inbox     # → dist/gmail_inbox-preview.html
make preview_app APP=gmail_composer

# Open dist/<app>-preview.html in any browser, OR assert it renders headless:
make preview_smoke APP=gmail_inbox   # Playwright, needs a Chromium
```

`dist/mcp-app.html` is committed, so `make preview_app` works out of the box.
If you changed an app's frontend, rebuild it first with `make build_apps`.

The generated `dist/<app>-preview.html` is a single self-contained file (the app
bundle is inlined as base64), which also makes it easy to hand to a viewer that
renders standalone HTML.

## Files

| File | Role |
|---|---|
| `src/host.ts` | Host bridge: no-client `AppBridge` + fixture dispatch |
| `src/fixtures.ts` | Canned tool responses + initial payload per app |
| `build.mjs` | Bundles the host, inlines the app bundle, writes the preview |
| `smoke.mjs` | Playwright smoke test (renders from fixtures, asserts paint) |

## Adding fixtures for a new app

1. Add the app's initial payload to `initialResult()` in `src/fixtures.ts`.
2. Handle its tool names in `dispatch()`.
3. `make preview_app APP=<name>`.

Not wired into `make ci` (needs `bun` + a browser) - it's a developer tool,
matching how `make build_apps` and the per-app `vitest` suites stay out of CI.
See `../MCP_UI_ARCHITECTURE.md` for the app architecture this previews.
