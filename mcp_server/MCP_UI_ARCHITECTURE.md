# MCP UI Architecture

## Enhancement Pattern
- Opt-in enhancers wrap pure services; only services that need MCP UI features get one.
- `EnhancedTool[TInput, TOutput]` typed wrapper class with capability detection
- Methods: `send_image`, `send_text`, `send_audio`, `send_app`, `elicit`, `call`
- Services stay pure `(Input) -> Output` - shared by CLI, API, MCP
- Enhancers are MCP-only plugins in `mcp_server/enhancers/`
- `@enhance("service_name")` decorator. Services without an enhancer run as headless tools.
- Headless tools = unchanged, zero overhead. Enhanced = async with Context.

## App Attachment
- Dynamic via `CallToolResult` with conditional `_meta.ui.resourceUri`
- SDK passes `_meta` through unchanged when tool returns `CallToolResult` directly
- `EnhancedTool.send_app()` dual-keys both `_meta.ui.resourceUri` (nested, current) and `_meta["ui/resourceUri"]` (flat, legacy) for compat with older hosts (early ChatGPT Apps SDK)

## Frontend
- React + Vite + vite-plugin-singlefile (NOT Preact - ceiling too low for Radix/Shadcn)
- Always use bun, never npm
- Thin rendering layer - zero business logic in frontend
- All logic stays in Python via app-only tools (`visibility: ["app"]`)
- Committed `dist/mcp-app.html` so template works without Node
- Optional `make build_apps` for developers modifying frontend
- CI stays Python-only

## Elicitation
- Separate Pydantic models for elicitation schemas (not derived from service input models)
- Return raw SDK `ElicitationResult` - no wrapper. Match on union variants (`AcceptedElicitation`, `DeclinedElicitation`, `CancelledElicitation`)
- Enhancers check `tool.can_elicit` internally (Option B)
- Schemas use only spec-strict primitives (`str`, `int`, `float`, `bool`, `Literal[...]`) - Python SDK accepts `list[str]` but spec doesn't, avoid for cross-client compat

## Content Annotations
- Both audiences (user + assistant) see content by default
- Opt-out via `audience=["user"]` or `audience=["assistant"]` parameter
- No spec-level capability negotiation for `ImageContent` / `AudioContent` - always emit, clients fall back gracefully

## Capability Handling
- Enhancers check capabilities internally: `can_elicit` (real), `can_show_app` (best-effort)
- `can_show_app`: defaults to `True` unless `MCP_DISABLE_APPS=1` env var. Spec ambiguity around `extensions` capability (SEP-1724/2133 unratified, reference host doesn't transmit it). No spec-correct way to detect; clients that ignore the meta key see only structured content.
- `fallback` param on `@enhance` decorator catches enhancer crashes → headless (does NOT catch capability mismatches - those are the enhancer's responsibility)
- If client supports nothing, enhancer decides what to do (not auto-skipped)

## Discovery
- Explicit imports in server.py (same pattern as services)

## Missing HTML Behavior
- Resource handler logs warning + returns empty stub HTML
- Enhancer falls through to non-app response

## Other
- `outputSchema` auto-generated from `output_model` for all tools (headless and enhanced)
- Wrapper returns Pydantic model directly (not dict). Try/except removed - exceptions propagate; FastMCP turns them into `isError: true` text responses
- Resources & Prompts: out of scope
- SDK pin: `mcp[cli]>=1.27.0,<2.0.0`
- Testing initial PR: pytest + vitest only
- Testing follow-up: Playwright via Sunpeak (issue #37), then Desktest E2E

## Directory Structure

```
mcp_server/
  server.py
  enhancers/
    __init__.py          # @enhance, EnhancedTool, registry
    base.py              # EnhancedTool class
    schemas.py           # Elicitation Pydantic models
    gmail.py             # Example: app attachment for compose / curate_inbox / update_draft
    config.py            # Example: image output
  app_tools/
    gmail_composer.py    # App-only tools (visibility: ["app"]) for the composer iframe
    gmail_inbox.py       # App-only tools for the inbox reader iframe
  apps/
    gmail_composer/
      src/Composer.tsx    # React thin rendering layer (draft editor)
      vite.config.ts
      package.json
      dist/mcp-app.html   # Built artifact (committed)
    gmail_inbox/
      src/Inbox.tsx       # React thin rendering layer (thread list + reader)
      vite.config.ts
      package.json
      dist/mcp-app.html
```

**Why:** Driven by the need to support MCP UI features (elicitation at limited-but-growing client adoption, MCP Apps shipping since late 2025) without breaking the template's pure-function service architecture. The enhancer pattern lets MCP-specific UI be opt-in per-tool while keeping CLI/API consumers unaffected.

**How to apply:** When implementing MCP UI, follow this architecture. Don't mix UI concerns into the service layer. All new MCP UI features go through enhancers.

**See also:** `MCP_UI_EDGE_CASES.md` for the full edge-case spec.
