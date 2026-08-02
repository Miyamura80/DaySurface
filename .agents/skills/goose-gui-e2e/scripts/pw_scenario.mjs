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
  // Input-plumbing diagnostics for the interact leg. `after` is what the inputs
  // held once applied; `before_click` is what they held when re-read at click
  // time, recorded BEFORE any repair so a remount stays visible; `reapplied`
  // says whether that repair ran. SKILL.md has the table for reading them.
  inputs: interact && (interact.fill || interact.check)
    ? { via: null, after: null, before_click: null, reapplied: false } : null,
  // Two DIFFERENT facts, kept apart on purpose: `click_enabled` is whether the
  // control was gated, `click_landed` is whether the click actually reached it
  // (observed in-page, not inferred from the driver). Enabled does NOT imply
  // landed - an enabled control can still miss on a detached node, unstable
  // coordinates, or an overlay - and conflating them is how a click that never
  // fired gets read as "input was fine, blame the round-trip".
  // click_enabled null = the click step was never reached; click_landed null =
  // that, or delivery couldn't be determined (the frame was swapped by the click).
  click_enabled: null, click_landed: null,
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

// The host recreates the app iframe on its own schedule (message-list re-render,
// auto-resize), detaching the Frame handle we captured. Re-resolve by the URL
// captured at discovery, NOT by findAppFrame: that matches on the scenario's
// pre-interaction `dom_contains`, and an interact leg exists precisely to change
// that text - a signed document no longer says "Awaiting your signature" - so a
// text-based re-resolve would fail exactly when it is needed. Throw rather than
// return a dead handle: every DOM read below is .catch()-guarded, so a detached
// frame would degrade into a silent "did not re-render" and get blamed on the
// server.
let appFrameUrl = null;
async function liveFrame(app, frame) {
  if (frame && !frame.isDetached()) return frame;
  const hits = [];
  for (const w of app.windows())
    for (const f of w.frames())
      if (f !== w.mainFrame() && !f.isDetached() && f.url() === appFrameUrl) hits.push(f);
  // A srcdoc/blank app iframe can share its URL with host chrome frames; fall
  // back to the text heuristic only when the URL doesn't single one out.
  const next = hits.length === 1 ? hits[0] : await findAppFrame(app);
  if (!next) throw new Error(`app iframe detached and not re-resolvable (url=${appFrameUrl})`);
  log(`app frame was swapped by the host; re-resolved via ${hits.length === 1 ? "url" : "dom text"}`);
  return next;
}

// Apply every input the scenario declares, and PROVE each reached the app's
// state rather than just the DOM node - see SKILL.md, "Why the inputs are
// verified rather than trusted", for why an unverified `fill` fails the leg
// silently and gets blamed on the server (issue #28). Idempotent, and it covers
// `fill` AND `check` together: the apps gate their submit control on all of that
// state at once. Throws if an input still hasn't taken, so the leg reports a
// plumbing failure instead of clicking a control it can't enable.
async function applyInputs(frame, interact) {
  let via = null;
  if (interact.fill) {
    const el = frame.locator(interact.fill.selector).first();
    await el.waitFor({ state: "visible", timeout: 6000 });
    await el.fill(interact.fill.value, { timeout: 6000 })
      .catch((e) => log("fill threw:", e.message.split("\n")[0]));
    via = "fill";
    if ((await el.inputValue().catch(() => "")) !== interact.fill.value) {
      await el.evaluate((node, v) => {
        if (!(node instanceof HTMLInputElement) && !(node instanceof HTMLTextAreaElement))
          throw new Error(`fill selector matched <${node.tagName.toLowerCase()}>, not a text control`);
        const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(node, v);
        node.dispatchEvent(new Event("input", { bubbles: true }));
      }, interact.fill.value);
      via = "native-setter";
    }
  }
  if (interact.check) {
    // The host auto-resizes the iframe while pages render, so a coordinate-based
    // click can land stale; fall back to a DOM-dispatched click, which fires the
    // same change event React listens to.
    const box = frame.locator(interact.check.selector).first();
    await box.check({ timeout: 6000, force: true }).catch(() => box.evaluate((el) => el.click()));
  }
  const got = await readInputs(frame, interact);
  if (!satisfied(got, interact)) throw new Error(`inputs did not take: ${JSON.stringify(got)}`);
  return { via, values: got };
}

// Read back what the declared inputs currently hold. Kept separate from
// applyInputs so the value observed at click time can be RECORDED before any
// repair overwrites it - otherwise "the app remounted and reset its state" is
// indistinguishable from "the value never landed".
async function readInputs(frame, interact) {
  const got = {};
  if (interact.fill)
    got.fill = await frame.locator(interact.fill.selector).first().inputValue().catch(() => null);
  if (interact.check)
    got.check = await frame.locator(interact.check.selector).first().isChecked().catch(() => null);
  return got;
}

const satisfied = (got, interact) =>
  (!interact.fill || got.fill === interact.fill.value) && (!interact.check || got.check === true);

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
    appFrameUrl = frame.url(); // identity for re-resolution if the host swaps the iframe
    log(`app iframe found (${appFrameUrl.slice(0, 80)}); matched [${result.matched}] missing [${result.missing}]`);
    // Screenshot the app iframe's owning window so the shot shows the rendered
    // app. Resolved fresh at each shot: `frame` is re-resolved during the
    // interact leg and can end up in a different window, and a PASS filed
    // against a screenshot of unrelated chrome is worse than no screenshot.
    const ownWin = () => app.windows().find((w) => w.frames().includes(frame)) || win;
    if (shot) { await ownWin().screenshot({ path: shot }); log("screenshot ->", shot); }

    // --- optional in-iframe interaction (scenario 2: click -> re-render) ---
    // Drive a real control INSIDE the sandboxed app iframe, then assert the DOM
    // that only the server's response can produce. The iframe's callServerTool
    // goes app -> Goose -> /mcp directly (NOT through the mock LLM), so a matched
    // re-render is proof of a genuine user-initiated round-trip - unfakeable here.
    if (result.rendered && interact) {
      try {
        if (result.inputs) {
          frame = await liveFrame(app, frame);
          const applied = await applyInputs(frame, interact);
          result.inputs.via = applied.via;
          result.inputs.after = applied.values;
          log(`inputs applied via ${applied.via}: ${JSON.stringify(applied.values)}`);
        }
        if (interact.click) {
          frame = await liveFrame(app, frame);
          // Re-read the inputs immediately before the click, and record what was
          // OBSERVED before repairing any of it - that reading is the only thing
          // separating "the value never landed" from "the app remounted and
          // dropped it", and a repair-first order would erase the difference.
          if (result.inputs) {
            result.inputs.before_click = await readInputs(frame, interact);
            result.inputs.reapplied = !satisfied(result.inputs.before_click, interact);
            if (result.inputs.reapplied) {
              log(`inputs lost before click (${JSON.stringify(result.inputs.before_click)}); re-applying`);
              // Keep the repair's `via`: applyInputs is the only place that can
              // detect the native-setter fallback, and a re-apply that needed it
              // is exactly the run worth reporting. Never downgrade - "fill
              // worked the first time" doesn't undo a harder second attempt.
              const again = await applyInputs(frame, interact);
              if (again.via === "native-setter") result.inputs.via = "native-setter";
            }
          }
          const btn = interact.click.selector
            ? frame.locator(interact.click.selector).first()
            : frame.getByText(interact.click.text, { exact: false }).first();
          // Ask the PAGE whether the click arrived, rather than inferring it
          // from the driver. Nothing on this side can prove delivery: `force`
          // skips the hit-target check, so Playwright reports a forced click as
          // sent even when an overlay swallowed it, and isEnabled() only reads
          // the disabled attribute. A capture-phase listener testing
          // composedPath is the one signal that says the event reached the
          // target - and "the app was asked to do something" is the whole
          // premise the interact leg's verdict rests on.
          // Key the listener on the target's SHAPE (tag + text), not on the node
          // itself. These apps re-render while we work - pdf_signer swaps nodes
          // as page images arrive - so a listener closing over the element sees
          // a click on its replacement and reports a false negative, which the
          // grading rule below would turn into a spurious red.
          const ackKey = await btn
            .evaluate((el) => ({ tag: el.tagName, text: (el.textContent || "").trim().slice(0, 80) }))
            .catch(() => null);
          if (ackKey)
            await frame.evaluate((k) => {
              window.__ackClick = false;
              document.addEventListener("click", (e) => {
                for (const n of e.composedPath())
                  if (n instanceof Element && n.tagName === k.tag &&
                      (n.textContent || "").trim().slice(0, 80) === k.text) {
                    window.__ackClick = true;
                    return;
                  }
              }, true);
            }, ackKey).catch(() => {});
          // The plain click already waits up to 6s for the control to be
          // enabled, so don't hand-roll that poll. What it does not do is say
          // WHY it gave up - hence the isEnabled() reading on the failure path.
          // The forced retry exists because the host auto-resizes the iframe, so
          // a first click can land on stale coordinates.
          let clicked = await btn.click({ timeout: 6000 }).then(() => true).catch(() => false);
          if (clicked) {
            result.click_enabled = true;
          } else {
            result.click_enabled = await btn.isEnabled().catch(() => false);
            if (!result.click_enabled)
              log("click target DISABLED after its actionability wait - a forced click cannot fire on it");
            clicked = await btn.click({ timeout: 6000, force: true }).then(() => true)
              .catch((e) => { log("forced click failed:", e.message.split("\n")[0]); return false; });
          }
          // Three ways to end up without an acknowledgement, and they are NOT
          // the same verdict.
          //   - No listener armed AND no click ever succeeded: there was nothing
          //     to click. A definite non-delivery, so `false`, so the leg fails.
          //     Folding this into "undetermined" would let a scenario with a
          //     stale click selector pass whenever its expected text happened to
          //     be on screen already.
          //   - No listener armed but a click DID succeed: arming resolves the
          //     locator before the click, so a target that only mounts in
          //     between leaves us un-armed on a click that really landed.
          //     Unknowable, not a non-delivery - `false` here would be a
          //     spurious red on a good run.
          //   - Detached frame: the click may well have landed and taken
          //     `__ackClick` with it when the host swapped the iframe.
          if (!ackKey) {
            result.click_landed = clicked ? null : false;
            log(clicked
              ? "target unresolvable when arming but a click succeeded; delivery undetermined"
              : "click target never resolved and no click succeeded - there was nothing to click");
          } else if (frame.isDetached()) {
            result.click_landed = null;
            log("frame swapped during the click; delivery undetermined");
          } else {
            result.click_landed = await frame
              .evaluate(() => window.__ackClick === true).catch(() => null);
          }
          if (result.click_landed === false)
            log("NO CLICK REACHED THE TARGET - the app was never asked to do anything");
        }
        let ibody = "";
        for (let i = 0; i < 30 && (result.interact_missing.length || result.interact_sel_missing.length); i++) {
          frame = await liveFrame(app, frame);
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
          (wantSel.length ? ` selectors matched [${result.interact_sel_matched}] missing [${result.interact_sel_missing}]` : "") +
          (result.inputs ? ` inputs=${JSON.stringify(result.inputs)}` : "") +
          (interact.click ? ` click_enabled=${result.click_enabled} click_landed=${result.click_landed}` : ""));
        if (shot) { await ownWin().screenshot({ path: shot }); } // reshoot the post-interaction DOM
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
