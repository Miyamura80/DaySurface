# MCP UI Edge Cases

Spec of edge cases for the MCP UI layer (enhancers, elicitation, MCP Apps, rich content). Pair with `MCP_UI_ARCHITECTURE.md` for design rationale.

## Capability gaps

| ID | Scenario | Expected behavior |
|---|---|---|
| C1 | Client does not support elicitation | Enhancer checks `tool.can_elicit`. If false, skip the elicit; fall through with default input. Never call `ctx.elicit()` blindly. |
| C2 | Client does not support MCP Apps | No spec-standard capability flag exists (SEP-1724/2133 unratified). `tool.can_show_app` returns `True` unless `MCP_DISABLE_APPS=1`. Clients that ignore `_meta.ui.resourceUri` see only the structured/text content. |
| C3 | Client renders neither apps nor images | Enhancer falls back to plain text via the structured output. The pure service result is always usable on its own. |
| C4 | Client claims a capability but doesn't honor it | No detection. The user sees a degraded UX. Acceptable - no fix in scope. |

## Enhancer failure modes

| ID | Scenario | Expected behavior |
|---|---|---|
| E1 | Enhancer raises an exception | If `@enhance(fallback="headless")`, log via `loguru.exception` and return the pure service result as a structured response. If `fallback="error"`, propagate (FastMCP returns `isError: true`). |
| E2 | Enhancer hangs or runs >30s | No timeout enforced by the enhancer layer. Tool transport timeouts apply. Enhancers should be fast or use the `tasks` capability (out of scope for this PR). |
| E3 | Enhancer mutates `tool.input` in place | Forbidden. Use `tool.call(override_input=...)` with `model_copy(update=...)`. Mutation breaks the pure-service invariant. |
| E4 | Enhancer registered for unknown service name | `get_enhancer()` returns the registration; service registration would skip silently. Tool registration logs a warning at startup if no matching service exists. |
| E5 | Concurrent elicits in nested enhancers | Not supported. Only one `await tool.elicit(...)` call per tool invocation. Multiple sequential elicits in one tool invocation are allowed. |

## Elicitation schema constraints

| ID | Scenario | Expected behavior |
|---|---|---|
| EL1 | Schema includes nested `BaseModel` | Spec forbids; SDK raises `TypeError` at registration. Use only flat `BaseModel` with primitive fields. |
| EL2 | Schema includes `list[str]` | Python SDK accepts; spec does not. Avoid for cross-client compat. Use repeated elicits or comma-separated `str` if needed. |
| EL3 | User accepts elicitation | `isinstance(r, AcceptedElicitation)` is `True`; `r.data` is the validated Pydantic instance. |
| EL4 | User declines | `isinstance(r, DeclinedElicitation)`. Treat as "user said no" - do not retry. |
| EL5 | User cancels (closes dialog) | `isinstance(r, CancelledElicitation)`. Treat as "abort the tool call gracefully" - return what we have so far. |
| EL6 | Client returns malformed data | SDK validates against the Pydantic schema before returning. Validation failure surfaces as a `ToolError`. |

## MCP App attachment

| ID | Scenario | Expected behavior |
|---|---|---|
| A1 | `dist/mcp-app.html` missing at runtime | Resource handler logs a warning, returns an empty HTML stub with a comment explaining the missing build. Enhancer falls through to non-app response. |
| A2 | `_meta.ui.resourceUri` set but resource not registered | Client receives a dangling URI. Mitigation: tool registration validates that referenced `ui://` resources exist; fails fast at server start. |
| A3 | Old host (early ChatGPT Apps SDK) reads only flat `_meta["ui/resourceUri"]` | `EnhancedTool.send_app()` dual-keys both `_meta.ui.resourceUri` and `_meta["ui/resourceUri"]` for compat. |
| A4 | App-only tool surfaces to LLM | `meta={"ui": {"visibility": ["app"]}}` is convention, not spec. Some clients will expose these to the LLM. Acceptable; documented limitation. For hard isolation, run a second `FastMCP` instance app-tools-only (out of scope). |
| A5 | App calls server tool that doesn't exist | `app.callServerTool({name})` round-trip returns `isError: true` with a "tool not found" message. Frontend handles via `ontoolresult` error branch. |
| A6 | Dashboard JS tries to access network/storage | Iframe is sandboxed by the host. Most hosts disallow `fetch`, `localStorage`, top-level navigation. Plan UI accordingly - all state through `callServerTool`. |
| A7 | Host doesn't implement `ui/update-model-context` | Where the push exists it is best-effort try/catch: on rejection the Sent/Discarded UI state is unaffected and no error is shown - the model just stays uninformed, which is the pre-push status quo. **Known gap:** only the orphaned `apps/gmail_composer/` implements this (`modelContext.ts`). The composer that actually ships - `InlineComposer` inside `apps/gmail_inbox/` - never calls `updateModelContext`, so after a user-initiated send or discard the model still believes the draft is open. See A12. |
| A8 | PDF signing on a host that neither hides app-only tools (A4) nor supports elicitation | Residual risk: such a host could let the model call `pdf_signer.sign` with a fabricated typed name - the soft visibility hint is the only pre-signature layer left. Signing therefore does NOT rely on visibility alone: (1) the server state machine only accepts a sign on `awaiting_signature`, a state entered exclusively via `pdf_request_signature`; (2) on elicitation-capable hosts a host-native confirmation dialog (which the model cannot answer) gates the seal; (3) every attempt - including aborts - lands in the audit trail (typed name, timestamp, channel, `confirmed_via_elicitation: false`), and the PAdES seal pins exactly what was signed, so a fabricated signature is detectable and attributable after the fact. Hard isolation (second FastMCP instance) remains out of scope per A4. **Transport note:** layer (2) additionally requires a stateful session (`server.mcp_stateless_http: false`, single replica) - under stateless HTTP the client's elicitation response cannot be correlated back to the per-request session (`ctx.elicit()` would hang; empirically verified, same mechanics as python-sdk issue 678 for sampling), so `check_client_capability` correctly reports unsupported (client_params is never populated) and the ceremony rests on layers (1) and (3) plus the in-app user gesture. Every elicitation call site MUST gate on the declared capability, never attempt-when-unknown. |
| A9 | A terminal in-app action (send, discard, sign) completes while an earlier write to the same object is still in flight | The terminal state MUST win regardless of reply order. Clearing a pending debounce timer is not sufficient: a request already on the wire cannot be recalled, and its late reply would otherwise overwrite the terminal state. Implement with a `closingRef` latch set when the action commits and re-checked **after** each await, immediately before any state write (an entry-only guard is useless - the in-flight call passed it long ago). The latch also gives single-flight on repeat clicks, and is released if the terminal action itself fails so the UI stays editable and retryable. A terminal state must additionally require positive server confirmation (e.g. a `message_id`), never a merely-resolved `callServerTool` - that call resolves on `isError: true` too, and a false terminal state is unrecoverable by construction. Implemented in `InlineComposer.tsx`; regression tests in `InlineComposer.test.tsx`. |
| A10 | Host re-instantiates an app iframe after the underlying object is gone | **Known gap.** `ontoolresult` is one-shot and host-to-app only, so a server cannot push state into an already-rendered app instance; on re-mount the host replays the tool result that instantiated the view. For a draft that has since been sent, `Inbox.tsx` sees the replayed `draft_id` and reopens the composer for an object Gmail has already deleted. The spec defers state persistence to future work and points at a server-issued `_meta.viewUUID` plus host-side storage; DaySurface uses no `viewUUID`, `localStorage`, or `onteardown`. Mitigation when built: reconcile on mount (refresh unconditionally, not only when fields arrive empty) and render an inert "already sent / no longer exists" state on a 404. |
| A11 | The model calls a mutating tool while an app for the same object is rendered | **Known gap.** `gmail_send` and `gmail_discard_draft` are plain LLM-facing services with no enhancer, so a model-driven send never reaches a rendered composer and the stale editable view remains the last visible UI. This is currently asserted as intentional (`tests/test_mcp_server.py`, `tests/test_mcp_e2e.py`), so changing it is a deliberate design change plus a test flip, not a bugfix. |
| A12 | An app directory exists but no enhancer ever sends its `ui://` URI | `_register_app_resources` walks `mcp_server/apps/` and registers a resource per directory, so an unreferenced app is still advertised to hosts and still force-included in the wheel. `apps/gmail_composer/` has been in this state since the initial commit - every composer tool sends `INBOX_URI`. It is not dead weight only: it holds the reference implementation of A7 and A9 (`modelContext.ts`, `closingRef`, the serialized write chain) plus send-path tests, none of which run against the composer that ships. Treat divergence between an orphaned app and its live replacement as a source of silent regressions: either delete the orphan after porting what is missing, or wire an enhancer to it. |

## Output schema

| ID | Scenario | Expected behavior |
|---|---|---|
| O1 | Service raises exception | Wrapper does NOT catch. Exception propagates; FastMCP converts to `isError: true` text response. The previous `{"error": str(e)}` dict path is removed. |
| O2 | Service returns object that doesn't match `output_model` | Pydantic validation error at `.model_validate()` boundary inside the wrapper. Becomes an `isError` response. |
| O3 | Output model field renamed in a refactor | Clients validating against `outputSchema` will break. Treat output models as a public API; use deprecation cycles. |
| O4 | Headless tool consumed via direct registry call (CLI/API) | Returns Pydantic model instance, not dict. CLI/API consumers must call `.model_dump()` themselves if they need a dict. **Behavior change from current.** |

## Rich content

| ID | Scenario | Expected behavior |
|---|---|---|
| R1 | Image too large for client to render inline | No size enforcement at the enhancer layer. Hosts may truncate or drop. Keep images <1 MB base64-encoded as a guideline. |
| R2 | `audience` annotation set to `["assistant"]` only | Client SHOULD hide from human user. Not all clients honor this - treat as a hint, not a guarantee. |
| R3 | Multiple `send_image` calls in one enhancer | All appended to the response `content` list in call order. No deduplication. |
| R4 | MIME type unsupported by client | Most clients fall back to showing metadata. No spec-level negotiation. |

## Build & packaging

| ID | Scenario | Expected behavior |
|---|---|---|
| B1 | User installs the wheel without running `make build_apps` | `dist/mcp-app.html` is committed and force-included in the wheel - works out of the box. |
| B2 | Developer modifies `App.tsx` but forgets to rebuild | `make build_apps` runs the React build; the committed `dist/mcp-app.html` is overwritten. Pre-commit hook (prek) does NOT auto-build; manual step. Add a test that compares `dist/mcp-app.html` mtime vs `src/` mtimes if drift becomes a problem. |
| B3 | `bun` not installed when `make build_apps` runs | Target fails fast with a clear error message. CI never invokes this target. |
| B4 | Adding a new MCP App | Create `mcp_server/apps/<name>/` mirroring `doctor_dashboard/`. Update `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml` to include the new `dist/mcp-app.html`. |

## Transport boundaries

| ID | Scenario | Expected behavior |
|---|---|---|
| T1 | Service called from CLI (`cli.py`) | Enhancers are not invoked. Pure service runs. No `Context`, no elicitation, no app meta. |
| T2 | Service called from API (`api_server/`) | Same as T1. Enhancers are MCP-only. |
| T3 | Service called from MCP without an enhancer | Headless path - returns Pydantic model directly via FastMCP. |
| T4 | Service called from MCP with an enhancer | Enhanced path - async, with `Context`, returns `CallToolResult`. |

## Testing

| ID | Scenario | Expected behavior |
|---|---|---|
| TT1 | Unit-testing an enhancer | Use `MockContext` helper. Stub `session.check_client_capability` and `session.elicit`. Don't try to mock `RequestContext` - too deep. |
| TT2 | E2E-testing an app in a real host | Covered by the `goose-gui-e2e` tier (issue #37, now built): the real Goose desktop app (Electron) mounts the `ui://` resource and Playwright asserts the rendered iframe DOM plus the tool round-trip log. Heavy - not in `make ci`; run via `make test_apps_e2e`, or `RUN_APPS_E2E=1` for the guarded pytest entry. See `.agents/skills/goose-gui-e2e/SKILL.md`. Scenarios cover render plus click-to-round-trip for settings, gmail_inbox, and pdf_signer; the composer's send path is **not** yet covered. |
| TT3 | Existing `tests/test_mcp_server.py` after `outputSchema` change | Registry-only assertions - no behavioral break. Add a check that enhanced tools are still listed. |

## Out of scope (intentionally)

- **Resources / Prompts** - no enhancer support; existing FastMCP primitives only
- **Sampling** - not used by any enhancer in this PR
- **Tasks (SEP-1686)** - long-running ops, separate work
- **Visual regression tests** - premature on a churning UI
- **Hard LLM/app tool isolation via separate FastMCP servers** - visibility convention is good enough for now
