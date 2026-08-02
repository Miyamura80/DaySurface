---
name: goose-gui-e2e
description: Drive the real Goose desktop GUI (Electron) as an MCP client via Playwright to end-to-end test DaySurface's MCP Apps - asserting the ui:// iframe actually renders and round-trips in a real host. Use to run, add, or debug MCP-App e2e scenarios.
---

# Goose-GUI end-to-end testing of the MCP Apps

Goose (Block's open-source agent, an Agentic AI Foundation reference MCP client)
renders MCP Apps natively - it fetches a tool's `ui://` resource and mounts the
HTML in a sandboxed iframe. This skill stands up the **real Goose desktop app**
(Electron), wires **DaySurface's `/mcp` mount** into it as a streamable-HTTP
extension, drives a chat scenario with **Playwright-Electron** (DOM selectors, no
pixel coordinates), and decides PASS/FAIL from two records the mock LLM cannot
fake: the **rendered iframe DOM** and the **tool-call round-trip log**.

This closes the L4 "rendered iframe in a real host" gap from issue #37 - the layer
`tests/test_mcp_e2e.py` (wire) and the per-app vitest (component) deliberately
don't cover. It's ported from Edison-Watch's `goose-gui-e2e` harness; the Goose
build under `$E2E_HOME/goose_src` is repo-agnostic and shared with it.

```
   scenario (JSON: prompt + tool plan + expect)
        │  Playwright-Electron drives the DOM
        ▼
   mock LLM ──emits webhook_settings──► Goose desktop GUI ──/mcp──► DaySurface server
   (scenario engine)                    (Electron, real)            (FastMCP + enhancer
        ▲                                     │  renders ui://       attaches ui://daysurface/settings)
        │                                     ▼
        └──────────────────────────  sandboxed iframe (Settings app)
                                             │
                     assert: iframe DOM (pw_result) + tool round-trip (toolcalls.jsonl)
```

## Quickstart

```bash
cd .agents/skills/goose-gui-e2e/scripts     # or the symlink under .claude/skills/
export E2E_HOME="$HOME/goose-e2e"           # working dir (default; override freely)

bash setup.sh                # ONE TIME: build goose + Electron + bundles (~6-8 min). Idempotent.
bash up.sh                   # seed e2e DB + API key; start DaySurface server + mock + vite (idempotent)
bash run_test.sh settings_render   # drive the GUI + assert -> PASS/FAIL, screenshot in $E2E_HOME
bash run_all.sh              # run every scenario, print a summary
bash down.sh                 # stop test services  (down.sh --reset also drops the e2e DB + key)
```

`make test_apps_e2e` runs `up.sh && run_all.sh`. Each `run_test.sh` writes a
screenshot of the real Goose GUI (with the Settings app rendered) to
`$E2E_HOME/shot_<scenario>.png`.

## Reporting a run (required)

The whole point of this tier is that a human can *see* the app rendered in a real
host. `run_all.sh`'s `7 passed, 0 failed` does not show that - it shows that a
script agreed with itself. So a run is only reported when the work is laid out in
this shape:

1. **State the run's provenance first**, in one line: mock or real LLM, whether
   `setup.sh` rebuilt or reused the Goose/Electron build, and the pinned Electron
   version. A verdict with no provenance can't be reproduced or trusted.
2. **A scenario table**, one row per scenario, in run order, with these columns:
   *Scenario* · *What the GUI is driven to do* · *What proves it* (iframe DOM,
   round-trip log, or both) · *Verdict*. Naming the proof is what separates this
   from a bare PASS - it says which of the two unfakeable records was consulted.
3. **Every screenshot surfaced inline, each with its own label.** Do not print a
   list of file paths and call it a preview - a path the reader has to go open is
   not a preview. Attach or embed the actual images (`$E2E_HOME/shot_<scenario>.png`,
   one per scenario) using whatever mechanism the running agent has for showing
   image files to the user. Each label names the scenario **and the state
   captured**, not just the filename - e.g. `settings_subscribe - after clicking
   "Add endpoint": signing secret rendered`, not `shot_settings_subscribe.png`.
   For scenarios with an `interact` block the label must say the post-interaction
   state, since that is the leg the screenshot is evidence for.
4. **Failures keep more evidence, not less.** A FAIL still gets its screenshot and
   label, plus `mcp_probe.py`'s reason line and the relevant `pw_scenario.mjs` /
   `toolcalls.jsonl` excerpt. Never summarize a failure as a count.
5. **Report what was skipped.** If a scenario didn't run - stack down, seed
   missing, deliberately excluded - say so explicitly next to the ones that did.
   A table that silently omits a scenario reads as full coverage when it wasn't.

## Where this runs in the suite

Heavy tier - **not** in `make ci` (which stays Node/Rust/Electron-free):

- **Local / make:** `make test_apps_e2e` (after the one-time `setup.sh`).
- **Full pytest suite:** `tests/test_apps_e2e.py` is a collectable member, **skipped
  by default**; opt in with `RUN_APPS_E2E=1` (e.g. `RUN_APPS_E2E=1 uv run pytest
  tests/test_apps_e2e.py --no-cov`). It shells out to `up.sh` + `run_all.sh`.
  (`--no-cov` keeps a single-file run from tripping `pytest.ini`'s coverage gate.)
- **CI:** `.github/workflows/apps_e2e.yaml` - `workflow_dispatch` + weekly cron. It
  builds Goose via `setup.sh` (open egress, so the allowlist preconditions below
  don't apply on GitHub runners) and runs the guarded pytest entry.

## Preconditions (verify before setup)

- **GitHub release-asset host on the network allowlist.** Electron's binary is the
  official GitHub release asset. `github.com` 302-redirects to
  `release-assets.githubusercontent.com` (Azure-backed) for the actual bytes, so in
  the environment's **Network access → Custom**, add both `github.com` and
  `release-assets.githubusercontent.com` (keep the default package-manager list
  checked). `setup.sh` preflights this and fails loudly if it's missing.
- Sandbox provides: `cargo`, `pnpm`/`node`, `bun`, `uv`, `xvfb-run`. `setup.sh`
  needs no egress-blocked paths - it uses `git clone` + the official release assets.

## How a scenario works

A scenario is JSON in `scripts/scenarios/<name>.json`:

```json
{
  "prompt": "Open my settings.",
  "plan": [ { "match": "webhook_settings" } ],
  "final": "Opened your settings.",
  "app_uri": "ui://daysurface/settings",
  "expect": {
    "tool_called": "webhook_settings",
    "round_trip": true,
    "app_rendered": true,
    "dom_contains": ["Settings", "Gmail"]
  }
}
```

- `prompt` - the user message typed into Goose.
- `plan` - the tool-call sequence the **mock** emits. `match` is a substring of
  the tool name Goose offers (Goose may namespace extension tools, so substring
  not exact). A plan step may carry `"args"` (e.g. `{"match": "gmail_get_thread",
  "args": {"thread_id": "t-1001"}}`) which the mock passes through as the tool
  call's arguments. Pick an **app-returning** tool: `webhook_settings` →
  `ui://daysurface/settings` renders offline with zero external deps, and
  `gmail_get_thread` → `ui://daysurface/gmail_inbox` renders offline **because the
  stack sets `GMAIL_FAKE_BACKEND=1`** (see below).
- `expect` - checked by `mcp_probe.py`: `tool_called` + `round_trip` (from the
  tool-call log) and `app_rendered` + `dom_contains` (from the rendered iframe).
- `seed` (optional) - a script in `scripts/` that `run_test.sh` runs before
  driving, for scenarios needing a fixture row the shared seed doesn't create
  (the `pdf_sign_*` scenarios use `seed_pdf_doc.py` for the `e2e-nda-doc` PDF).

### Rendering the Gmail apps offline (`GMAIL_FAKE_BACKEND`)

The `gmail_inbox` / `gmail_composer` apps normally need a linked Google account:
`gmail_get_thread` → `_get_gmail_client()` raises `GmailNotConnectedError`
(`ConnectRequiredError`) with no token row, so the tool errors **before**
`send_app` and the iframe never mounts. To render them offline, `up.sh` starts
the server with `GMAIL_FAKE_BACKEND=1`, which makes `_get_gmail_client` return a
fixture-serving fake (`services/_gmail_fake_backend.py`) instead of hitting
Google - no linked account, OAuth, or network. The fixtures are raw-Gmail-API
shaped, so the *real* service parsing → Pydantic `GmailThread` runs end-to-end;
only the network boundary is faked. The flag is **hard-refused under
`DEV_ENV=prod`** (guard in `services.gmail_svc._maybe_fake_gmail_client`), so it
can never stand in for a real mailbox in production.

**Why the fake also serves the curated inbox:** the `gmail_inbox` app registers
its `ontoolresult` handler in a mount effect, and if the host delivers the thread
result *before* that runs, the app misses it and falls back to a curated-inbox
refresh (`gmail_inbox.refresh` → `gmail_curate_inbox`) after ~800ms. So a
thread-open renders the **reader** when it wins that race and the **curated list**
when it loses. The fake serves both paths from the same fixture thread, and
`gmail_thread_render` asserts on content present in both views, so the scenario is
deterministic instead of ~30% flaky. `gmail_get_thread`'s round-trip is still
proven independently via the tool-call log regardless of which view renders.

### Optional: a click → `callServerTool` → re-render step

A scenario may add an `interact` block to drive a real control **inside** the app
iframe after it renders, exercising a **user-initiated** server round-trip (the
`settings_subscribe` fixture does this - clicks "Add endpoint" → `settings.subscribe`):

```json
"expect": { "...": "...", "interaction_rendered": true },
"interact": {
  "fill":  { "selector": "input[type=url]", "value": "https://webhook.example.com/e2e" },
  "check": { "selector": "input[type=checkbox]" },
  "click": { "text": "Add endpoint" },
  "expect_dom_contains": ["Signing secret", "webhook.example.com"]
}
```

`fill` and `check` are both optional and are applied together before the click
(`pdf_sign_ceremony` uses both - a typed name plus a consent box). `click` takes
either a `"selector"` or a `"text"` substring.

The iframe's `callServerTool` goes **app → Goose → `/mcp` directly, bypassing the
mock LLM**, so this round-trip never appears in the tool-call log - the re-rendered
DOM (`expect_dom_contains`, here the returned signing secret) is the only proof,
and only the server's real response can produce it. For post-interaction DOM that
plain text can't see, `interact` also accepts `"expect_selectors": ["css", ...]` -
CSS selectors that must each match at least one element (e.g.
`img[src^="data:image/png;base64,"]` to prove a fetched image's data URI was
written into the iframe DOM - presence, not paint). `interaction_rendered: true`
makes `mcp_probe.py` require it, and `pw_scenario.mjs` fails the drive if the
post-click DOM never matches. This needs the "Add endpoint" control, which renders
only when `push_available` is true - `up.sh` sets `GMAIL_PUBSUB_TOPIC` to ungate it
(no Pub/Sub is ever contacted).

#### Why the inputs are verified rather than trusted

Playwright's `fill` types through CDP into the *focused* element and never reads
the value back. Inside a sandboxed cross-origin iframe that can silently miss, so
a React-controlled input keeps its empty `value` - and these apps gate their
submit control on that state: `Settings.tsx` on `busy || !url.trim()`,
`Signer.tsx` on the typed name **and** the consent box **and** the phase. A gated
button absorbs both click paths without raising: the plain click fails its
enabled-actionability check, and a *forced* click dispatches nothing, because
browsers don't fire `click` on a disabled element. The leg fails as "interaction
did not re-render" and reads like a server problem (issue #28).

So `applyInputs` sets `fill` and `check` **together** and proves each landed,
falling back to the native value setter plus a bubbling `input` event - what
React's synthetic `onChange` listens for, and what its instance-level value shim
can't suppress. It runs again immediately before the click if a re-read shows the
state was lost (a host-triggered remount resets it), repairing every input rather
than just the text one - re-applying the name alone would leave `pdf_signer`'s
consent box unticked and the button still shut.

`inputs`, `click_enabled` and `click_landed` land in `pw_result_<name>.json` and
on `mcp_probe.py`'s always-printed summary line. `before_click` records what was
**observed**, before any repair, which is what makes these failure modes
distinguishable at all. `click_enabled` and `click_landed` are deliberately
separate, and the implication runs one way only: **enabled does not imply
landed.** An enabled control can still be missed - a detached node, unstable
coordinates, an overlay swallowing the hit - and `force` skips the hit-target
check, so Playwright reports a forced click as *sent* regardless. So
`click_landed` isn't inferred from the driver at all: a capture-phase listener
inside the iframe reports whether the event actually reached the target. Only
that means the app was asked to do something, and `mcp_probe.py` **fails** a
scenario whose expected DOM matched while `click_landed` is `false` - otherwise
text that was already on screen would grade as a round-trip.

| `inputs.after` | `inputs.before_click` | `click_enabled` / `click_landed` | Read it as |
| --- | --- | --- | --- |
| throws `inputs did not take` | - | `null` / `null` | the value never reached the app - driver-side plumbing |
| set | not satisfied (`reapplied: true`) | - | the app remounted between apply and click and reset its state |
| set | set | `false` / `false` | the control is gated on something else (a `busy` flag, a phase) |
| set | set | `true` / `false` | the control was fine but the click never arrived - overlay, or a stale hit target |
| set | set | `true` / `true` | input and click were fine - the round-trip or the re-render is the real failure |
| set | set | `true` / `null` | delivery undetermined: the click swapped the iframe, taking the acknowledgement with it |
| any | any | `null` / `null` | the click step was never reached; check the `interaction ERROR` line |

**A repaired PASS is not a clean PASS.** `via: "native-setter"` or
`reapplied: true` means the harness drove the app harder than a user could - if
the control is genuinely undriveable, or the app really does drop typed input on
remount, that is a bug a real user hits. `mcp_probe.py` prints a `DEGRADED:` line
next to the PASS for exactly this reason; carry it into the run report rather than
reporting a bare pass.

The frame handle is re-resolved before each step (`liveFrame`), because the host
recreates the app iframe on message-list re-renders and auto-resizes. It matches
on the iframe **URL** captured at discovery, not on `expect.dom_contains`:
`findAppFrame`'s text heuristic is only valid for the initial discovery, since an
`interact` leg exists precisely to change that text (a signed document no longer
says "Awaiting your signature"), so a text-based re-resolve would fail exactly
when it is needed. It throws rather than returning a detached handle - every DOM
read is `.catch()`-guarded, so a dead handle would degrade into a silent "did not
re-render" and get blamed on the server.

## Why the mock cannot fake a PASS

The mock only follows the plan and returns canned narration; it never inspects
tool results. The verdict is computed by `mcp_probe.py` from two records the mock
doesn't control:
- **the rendered iframe** - `pw_scenario.mjs` frameLocators into the real Goose
  DOM and asserts the Settings app's text. The iframe only exists if the server
  returned the app resource and Goose fetched + mounted it - a full round-trip.
- **the tool-call log** - the calls Goose actually issued to `/mcp` and the
  results the server returned.

Two gates stop a no-op run from passing on stale data:
- **Drive must render.** `pw_scenario.mjs` exits non-zero unless the app iframe
  rendered with the expected DOM; `run_test.sh` checks it (`PIPESTATUS[0]`).
- **Log must be new.** `run_test.sh` snapshots the tool-call-log line count before
  driving; `mcp_probe.py` FAILs if no new entries were appended for this run.

## Mock vs. real LLM

- **Mock (default):** deterministic, offline, CI-friendly. `configure_goose.sh mock`.
- **Real LLM:** `configure_goose.sh real anthropic $KEY` (model via `$GOOSE_REAL_MODEL`).
  The agent decides tool calls from the `prompt`; requires an API key **and** the
  provider host on the allowlist. The render/round-trip assertions are unchanged.

## Sandbox gotchas already handled (do not re-discover)

Baked into the scripts; listed so you recognize them if something drifts:

| Symptom | Cause → handled by |
|---|---|
| Electron binary 403 | a download host not allowlisted → `setup.sh` preflights by **fetching a real release asset** (a bare-root probe can't tell a proxy's 403 denial from a healthy root) and names the blamed host; official download + checksum |
| `@electron/node-gyp` 403 on `pnpm install` | git tarball on codeload blocked → registry override (`setup.sh`) |
| Electron SIGSEGV "Missing X server" | needs a display → `xvfb-run` owns Xvfb per test |
| Electron crash under Xvfb | `--no-sandbox --disable-gpu` (NOT `--in-process-gpu`) |
| CDP `/json` empty / debug-port hang | Playwright-Electron launches over a CDP pipe |
| bg service dies next tool call | `setsid ... & disown` (`start_detached`); re-run `up.sh` to recover |
| `/mcp` 401 | the extension must send the seeded `X-API-KEY` header (`configure_goose.sh`) |
| app iframe not found | assert only on an **app-returning** tool; the Settings app renders offline |

## Files

| File | Role |
|---|---|
| `lib.sh` | paths/ports + bounded-wait/`setsid` helpers |
| `setup.sh` | one-time provisioning: build goose, Electron from official release assets, pnpm install, dev bundle |
| `seed.py` | create the e2e SQLite schema + one API key; prints the raw key |
| `up.sh` / `down.sh` | idempotent stack bring-up / teardown |
| `configure_goose.sh` | write Goose config for `mock`/`real`, wiring the `daysurface` extension at `/mcp` |
| `mock_llm.py` | scenario-engine mock LLM (follows the plan, logs tool calls) |
| `pw_scenario.mjs` | Playwright-Electron driver + rendered-iframe DOM assertion |
| `mcp_probe.py` | assertion oracle - rendered iframe (`pw_result`) + tool round-trip (`toolcalls.jsonl`) |
| `run_test.sh` / `run_all.sh` | drive + assert one / all scenarios |
| `scenarios/settings_render.json` | scenario 1: LLM opens the Settings app; assert it renders |
| `scenarios/settings_subscribe.json` | scenario 2: click "Add endpoint" → `settings.subscribe` → assert the re-render |
| `scenarios/gmail_thread_render.json` | scenario 3: LLM opens a thread via `gmail_get_thread`; assert the gmail_inbox reader iframe renders (needs `GMAIL_FAKE_BACKEND`) |
| `scenarios/gmail_remote_images.json` | scenario 4: remote-image pipeline - assert the blocked-by-default "Show images" banner renders, click it, and assert the `gmail_inbox.fetch_image` round-trip re-renders as "Retry" (fixture URL is `.invalid`, so the SSRF guard rejects it deterministically offline and in CI) |
| `scenarios/pdf_sign_render.json` | scenario 5: `pdf_request_signature` → assert the `pdf_signer` app renders the awaiting-signature state |
| `scenarios/pdf_sign_ceremony.json` | scenario 6: type the legal name, tick consent, click "Sign document" → assert "Signed by …" plus both rendered page images |
| `scenarios/pdf_sign_cancel.json` | scenario 7: same render, click "Cancel" → assert the ceremony aborts with "Signing cancelled" |
| `seed_pdf_doc.py` | per-scenario seed for the pdf scenarios (the `seed` key below); inserts the `e2e-nda-doc` fixture |

Also outside the skill: `tests/test_apps_e2e.py` (guarded pytest entry) and
`.github/workflows/apps_e2e.yaml` (opt-in CI).
