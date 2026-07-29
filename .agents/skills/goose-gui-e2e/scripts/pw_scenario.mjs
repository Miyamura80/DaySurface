// Drive one MCP-App e2e scenario through the real Goose desktop GUI via
// Playwright-Electron, then ASSERT ON THE RENDERED ui:// IFRAME - the L4 gap that
// motivated DaySurface issue #37.
//   node pw_scenario.mjs <scenario.json> <shotPath>
//
// Flow: type the prompt -> the mock emits the app-returning tool call -> the
// DaySurface server returns the CallToolResult with _meta.ui -> Goose fetches the
// ui:// resource and renders it in a sandboxed iframe -> we frameLocator into that
// iframe and assert its DOM. The render is proof of the full round-trip (server
// returned the app + the iframe received its ontoolresult payload); it cannot be
// faked by the mock, which never produces the settings DOM.
//
// Writes $E2E_HOME/pw_result_<name>.json for the oracle (mcp_probe.py) and a
// screenshot. Exits non-zero on any drive/render failure so run_test.sh aborts
// before grading (a no-op run must FAIL, never fall through to a stale verdict).
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const PW = process.env.PLAYWRIGHT || "playwright";
const pkg = (await import(PW)).default ?? (await import(PW));
const { _electron: electron } = pkg;

const GOOSE_SRC = process.env.GOOSE_SRC;
const CURRENT = process.env.CURRENT_SCENARIO;
const E2E_HOME = process.env.E2E_HOME;
const VITE_PORT = process.env.VITE_PORT || "5173";
const scenarioPath = process.argv[2];
const shot = process.argv[3];
const sc = JSON.parse(readFileSync(scenarioPath, "utf8"));
const name = path.basename(scenarioPath).replace(/\.json$/, "");
const resultPath = path.join(E2E_HOME, `pw_result_${name}.json`);
writeFileSync(CURRENT, JSON.stringify(sc)); // hand the scenario to the running mock

const expect = sc.expect || {};
const want = expect.dom_contains || [];
const log = (...a) => console.log("[scenario]", ...a);

const MOCK_PORT = process.env.MOCK_PORT || "8410";
const env = {
  ...process.env, // DISPLAY inherited from xvfb-run
  GOOSE_BINARY: `${GOOSE_SRC}/target/debug/goose`,
  GOOSE_DISABLE_KEYRING: "1",
  // Route Goose's OpenAI provider at the local mock (NOT api.openai.com). Set in the
  // launch env - the config/secrets file alone does not override the base URL.
  GOOSE_PROVIDER: "openai", GOOSE_MODEL: "gpt-4o-mini",
  OPENAI_API_KEY: "sk-mock",
  OPENAI_BASE_URL: `http://127.0.0.1:${MOCK_PORT}/v1`,
  OPENAI_HOST: `http://127.0.0.1:${MOCK_PORT}`,
  ELECTRON_DISABLE_SANDBOX: "1", LIBGL_ALWAYS_SOFTWARE: "1",
  NODE_ENV: "development", GOOSE_TUNNEL: "no",
};

const interact = sc.interact || null;
const wantI = (interact && interact.expect_dom_contains) || [];
// Optional CSS-selector assertions for post-interaction DOM that plain text
// can't see (e.g. an <img src="data:image/..."> painted after a fetch).
const wantSel = (interact && interact.expect_selectors) || [];
const result = {
  scenario: name, rendered: false, matched: [], missing: want, app_uri: sc.app_uri || null, frames: 0,
  // Second-scenario (click -> callServerTool -> re-render) fields. `interacted` is
  // the proof of a USER-initiated round-trip: the iframe called /mcp directly
  // (bypassing the mock LLM) and the returned data re-rendered into the DOM.
  interacted: interact ? false : null, interact_matched: [], interact_missing: wantI,
  interact_sel_matched: [], interact_sel_missing: wantSel,
};
const writeResult = () => writeFileSync(resultPath, JSON.stringify(result, null, 2));

// Scan every frame of every Goose window for the app iframe. The MCP App renders
// in a *child* sandboxed iframe, so we skip each window's main frame (that's
// Goose's own chrome, which also contains the word "Settings" in its nav) and
// require ALL expected texts - which uniquely identifies the app, not the host UI.
// Sandboxed cross-origin iframes are still readable via Playwright in Electron.
async function findAppFrame(app) {
  for (const win of app.windows()) {
    const main = win.mainFrame();
    for (const frame of win.frames()) {
      if (frame === main) continue; // Goose's own renderer, not the app iframe
      try {
        const txt = await frame.locator("body").innerText({ timeout: 1500 });
        if (txt && want.length && want.every((w) => txt.includes(w))) return frame;
      } catch { /* frame detached / navigating / not yet ready */ }
    }
  }
  return null;
}

const app = await electron.launch({
  executablePath: `${GOOSE_SRC}/ui/node_modules/electron/dist/electron`,
  args: [`${GOOSE_SRC}/ui/desktop/.vite/build/main.js`, "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  env, timeout: 90000,
});
let ok = false;
try {
  let win = await app.firstWindow();
  for (let i = 0; i < 20 && !win.url().includes(VITE_PORT); i++)
    win = await app.waitForEvent("window", { timeout: 5000 }).catch(() => win);
  await win.waitForLoadState("domcontentloaded");
  await win.getByText("New Chat", { exact: false }).first().waitFor({ timeout: 30000 });

  // Dismiss the "Help improve goose" telemetry modal (text, not a role=button;
  // its overlay swallows pointer events and can race the New Chat click).
  const dismissModal = () =>
    win.getByText(/^no thanks$/i).first().click({ timeout: 4000 }).then(() => true).catch(() => false);
  await dismissModal();

  const clickNewChat = () => win.getByText("New Chat", { exact: true }).first().click({ timeout: 15000 });
  await clickNewChat().catch(async () => { await dismissModal(); await clickNewChat(); });

  const box = win.locator("textarea").first();
  await box.waitFor({ timeout: 10000 });
  await box.click();
  await box.fill(sc.prompt);
  await win.keyboard.press("Enter");
  log("prompt sent:", sc.prompt);

  // Poll for the app iframe to render (tool round-trip + ui:// fetch + mount).
  let frame = null;
  for (let i = 0; i < 45 && !frame; i++) {
    frame = await findAppFrame(app);
    if (!frame) await win.waitForTimeout(1000);
  }
  result.frames = app.windows().reduce((n, w) => n + w.frames().length, 0);

  if (frame) {
    const body = await frame.locator("body").innerText({ timeout: 4000 }).catch(() => "");
    result.matched = want.filter((t) => body.includes(t));
    result.missing = want.filter((t) => !body.includes(t));
    result.rendered = result.matched.length === want.length && want.length > 0;
    log(`app iframe found; matched [${result.matched}] missing [${result.missing}]`);
    // Screenshot the app iframe's owning window so the shot shows the rendered app.
    const ownWin = app.windows().find((w) => w.frames().includes(frame)) || win;
    if (shot) { await ownWin.screenshot({ path: shot }); log("screenshot ->", shot); }

    // --- optional in-iframe interaction (scenario 2: click -> re-render) ---
    // Drive a real control INSIDE the sandboxed app iframe, then assert the DOM
    // that only the server's response can produce. The iframe's callServerTool
    // goes app -> Goose -> /mcp directly (NOT through the mock LLM), so a matched
    // re-render is proof of a genuine user-initiated round-trip - unfakeable here.
    if (result.rendered && interact) {
      try {
        if (interact.fill)
          await frame.locator(interact.fill.selector).first().fill(interact.fill.value, { timeout: 6000 });
        // Optional checkbox tick (e.g. the pdf_signer consent box). The host
        // auto-resizes the iframe while pages render, so a coordinate-based
        // click can land stale; fall back to a DOM-dispatched click (fires
        // the same change event React listens to) when Playwright's check
        // can't get a stable hit.
        if (interact.check) {
          const box = frame.locator(interact.check.selector).first();
          await box.check({ timeout: 6000, force: true }).catch(() =>
            box.evaluate((el) => el.click())
          );
        }
        if (interact.click) {
          const btn = interact.click.selector
            ? frame.locator(interact.click.selector).first()
            : frame.getByText(interact.click.text, { exact: false }).first();
          // Host auto-resizes the iframe while pages render; a first click
          // can land on stale coordinates. Same fallback as the check step.
          await btn.click({ timeout: 6000 }).catch(() =>
            btn.click({ timeout: 6000, force: true })
          );
        }
        let ibody = "";
        for (let i = 0; i < 30 && (result.interact_missing.length || result.interact_sel_missing.length); i++) {
          ibody = await frame.locator("body").innerText({ timeout: 2000 }).catch(() => "");
          result.interact_matched = wantI.filter((t) => ibody.includes(t));
          result.interact_missing = wantI.filter((t) => !ibody.includes(t));
          const selHits = [];
          for (const sel of wantSel)
            selHits.push(await frame.locator(sel).count().catch(() => 0) > 0 ? sel : null);
          result.interact_sel_matched = wantSel.filter((s, j) => selHits[j] === s);
          result.interact_sel_missing = wantSel.filter((s, j) => selHits[j] !== s);
          if (!result.interact_missing.length && !result.interact_sel_missing.length) break;
          await win.waitForTimeout(700);
        }
        result.interacted =
          (wantI.length > 0 || wantSel.length > 0) &&
          result.interact_missing.length === 0 &&
          result.interact_sel_missing.length === 0;
        log(`interaction: matched [${result.interact_matched}] missing [${result.interact_missing}]` +
          (wantSel.length ? ` selectors matched [${result.interact_sel_matched}] missing [${result.interact_sel_missing}]` : ""));
        if (shot) { await ownWin.screenshot({ path: shot }); } // reshoot the post-interaction DOM
      } catch (e) {
        log("interaction ERROR", e.message.split("\n")[0]);
      }
    }
  } else {
    log("app iframe NOT found in any window/frame; dumping frame tree:");
    for (const w of app.windows()) {
      for (const f of w.frames()) {
        let snip = "";
        try { snip = (await f.locator("body").innerText({ timeout: 1500 })).replace(/\s+/g, " ").slice(0, 120); } catch (e) { snip = `<no-text: ${e.message.split("\n")[0]}>`; }
        log(`  frame url=${f.url().slice(0, 80)} main=${f === w.mainFrame()} text="${snip}"`);
      }
    }
    if (shot) { await win.screenshot({ path: shot }).catch(() => {}); }
  }

  writeResult();
  ok = result.rendered && (!interact || result.interacted);
  const why = !result.rendered
    ? "app not rendered / DOM mismatch"
    : interact && !result.interacted
      ? "interaction did not re-render"
      : interact ? "app rendered + interaction re-rendered" : "app rendered";
  console.log(ok ? `DRIVE: OK (${why})` : `DRIVE: FAIL (${why})`);
} catch (e) {
  console.log("DRIVE: ERROR", e.message.split("\n")[0]);
  writeResult();
  try { if (shot) { const w = await app.firstWindow(); await w.screenshot({ path: shot }); } } catch {}
} finally {
  await app.close();
}
process.exit(ok ? 0 : 1);
