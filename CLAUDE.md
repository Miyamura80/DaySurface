# CLAUDE.md

## Project Overview

Super-opinionated Python template that ships **one codebase, three interfaces** (CLI, MCP server, HTTP API) over a shared service registry. Python >= 3.12 required. Uses `uv` for dependency management (not pip).

**Before any other work in this repo, enable prek:** `uv tool install prek && prek install`. Hooks are defined in `prek.toml`.

**MCP terminology:** For nuances around frequently-confused MCP terms (Host vs. Client vs. Server, Tools vs. Resources vs. Prompts, Roots vs. Resources, transports, OAuth pitfalls, etc.), see [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md). Consult it before naming or designing new MCP-related code.

**MCP Apps:** an MCP extension for interactive iframe-sandboxed UIs (HTML resources via `ui://` URIs) embedded in chat clients, with bidirectional `postMessage` / JSON-RPC communication. See [ext-apps](https://github.com/modelcontextprotocol/ext-apps). *Not* a generic word for "MCP application".

**MCP is an actively-evolving spec.** Behaviors change frequently (transports, auth, primitives). Don't rely on training-data assumptions for anything MCP-related - always verify against the current spec via a fresh web search before writing or reviewing MCP code.

## Common Commands

```bash
# Setup
make all            # Sync dependencies

# Testing
make test           # Run pytest on tests/
make test_fast      # Run fast tests (no slow/nondeterministic)
make test_flaky     # Repeat fast tests to detect flakiness
make test_slow      # Run slow tests only
make test_nondeterministic # Run nondeterministic tests only
make mcp_conformance # MCPJam apps + protocol conformance vs local /mcp (needs node; CI-gated separately)

# Code Quality (run after major changes)
make fmt            # Run ruff formatter + JSON formatting
make ruff           # Run ruff linter
make vulture        # Find dead code
make ty             # Run type checker
make lint_links     # Check for broken links in markdown files (README, etc.)
make ci             # Run all CI checks (ruff, vulture, ty, import_lint, docs_lint, check_deps, lint_links, file_len_check, blind_except_check)

# Dependencies
uv sync             # Install dependencies (not pip install)
uv add <pkg>        # Add new dependency
uv run python <file> # Run Python files
uv run pytest path/to/test.py  # Run specific test

# Release
# 1. Update version in pyproject.toml
# 2. Tag the commit: git tag -a v0.1.0 -m "Release v0.1.0"
# 3. Push the tag: git push origin v0.1.0 (triggers Release workflow)
```

## Architecture

Layering (top calls down, never the reverse):

- **Transports:** `src/cli/app.py` (Typer) · `mcp_server/` (FastMCP) · `api_server/` (FastAPI)
- **Services:** `services/` - pure `@service`-decorated functions, transport-agnostic
- **Contracts:** `models/` - Pydantic input/output schemas shared by all transports
- **Infra:** `common/` (config) · `db/` (SQLAlchemy + Alembic) · `utils/llm/` (DSPY) · `src/utils/` (logging, theme, errors)

### Top-level layout

- **`src/cli/app.py`** + **`src/cli/commands/`** - Typer CLI (`mymcp`); `src/cli/commands/__init__.py` auto-discovers `src/cli/commands/*.py` and registers them.
- **`mcp_server/`** - FastMCP server. Primary transport is **streamable HTTP**, mounted on the FastAPI app at `/mcp` from `api_server/server.py` (one process, one port, shared auth/CORS). Stdio is legacy/dev-only via `mymcp-mcp`. `mcp_server/server.py:build_mcp_server` populates a single FastMCP singleton from the service registry; `mount_on` + `lifespan` handle the streamable-HTTP wiring. See [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md).
- **`api_server/`** - FastAPI HTTP server (`auth/`, `billing/`, `middleware/`, `routes/`). Hosts the `/mcp` mount; the primary entrypoint is `mymcp-serve`.
- **`services/`** - `@service(name=, description=, input_model=, output_model=)`-decorated pure functions (`services/__init__.py:20`).
- **`common/`** - pydantic-settings config.
  - `global_config.yaml` - base; `<name>.yaml` - split configs loaded as root key `<name>`
  - `production_config.yaml` - overlay loaded with high priority when `DEV_ENV=prod`
- **`src/`** - CLI plumbing (`src/cli/`) + shared utils (logging, theme, errors, output).
- **`utils/llm/`** - DSPY + LiteLLM wrapper with fallback model, Tenacity retries, LangFuse observability.
- **`tests/`** - subclass `TestTemplate` (`tests/test_template.py:14`) for per-test config isolation.
- **`docs/`** - Next.js + Fumadocs site; English source in `docs/content/en/`.
- **`landing-page/`** - standalone Astro + Tailwind v4 marketing site, **separate** from `docs/` and the Python server, with its own Railway deploy (`landing-page/railway.toml`). Data-driven: all copy lives in `src/config/landing.ts`, design tokens in `src/styles/global.css` (`@theme`). Uses `bun`, never npm. See [`landing-page/README.md`](./landing-page/README.md).
- **`.claude/`**, **`.agents/`**, **`.codex/`** - Claude/Codex agents and skills kept in sync by `scripts/sync_agent_config.py` (pre-commit enforced).

**Don't add new files at the repo root** unless tooling requires it. Nest under an existing folder.

### Adding a new feature

1. Pydantic models in `models/<feature>.py`.
2. Pure `@service` function in `services/<feature>_svc.py`. Pass `mutating=True` if it has side effects (create/charge/send).
3. (CLI) Typer command in `src/cli/commands/<feature>.py` calling the service.
4. (MCP) Nothing - `mcp_server/server.py` auto-registers on import.
5. (HTTP, optional) Route in `api_server/routes/`.
6. Tests inheriting `TestTemplate`.

`mutating=True` services get REST `Idempotency-Key` enforcement on their auto-generated API route: the key is required, claimed in the `idempotency_keys` table, and the response replayed on retries (same key + different payload → 422). Logic in `api_server/idempotency.py:execute_idempotent`. CLI unaffected. On MCP, `mutating` also gates the enhancer crash-fallback (`mcp_server/_tool_factory.py`): a mutating service is never silently re-executed - the fallback reuses the already-completed `tool.call()` result or propagates the enhancer error.

## Code Style

Enforced by ruff - see `[tool.ruff]` in `pyproject.toml`. Run `make fmt` and `make ruff`.

### Error handling

Broad catches like `except Exception:` and `except BaseException:` are banned by `ruff`'s `BLE001` rule; bare `except:` is separately banned by `E722` (part of the `E` select). Either narrow the catch to the concrete exception types you expect, or - if the call site is a genuine defensive boundary (middleware, health probe, background task, third-party SDK) - suppress with `# noqa: BLE001` *and* a justification comment on the next line (or trailing the same line) explaining why a broad catch is correct. `scripts/check_blind_except_justification.py` (wired into `make ci` and prek) fails the build if any `# noqa: BLE001` lacks a justification.

## Configuration Pattern

```python
from common import global_config

global_config.example_parent.example_child
global_config.llm_config.default_model
global_config.OPENAI_API_KEY  # secrets from .env
```

## LLM Inference Pattern

```python
from utils.llm.dspy_inference import DSPYInference
import dspy

class MySignature(dspy.Signature):
    input_field: str = dspy.InputField()
    output_field: str = dspy.OutputField()

inf_module = DSPYInference(pred_signature=MySignature, observe=True)
result = await inf_module.run(input_field="value")
```

## Testing Pattern

```python
from tests.test_template import TestTemplate
from tests.conftest import slow_test, nondeterministic_test

class TestMyFeature(TestTemplate):
    def test_something(self):
        assert self.config is not None

    @slow_test
    def test_slow_operation(self):
        pass
```

## Logging

```python
from loguru import logger as log
from src.utils.logging_config import setup_logging

setup_logging()
log.debug("detailed diagnostic information")
log.info("general informational message")
log.warning("warning message for potentially harmful situations")
log.error("error message for error events")
```

## Commit Message Convention

Use emoji prefixes indicating change type and magnitude (multiple emojis = 5+ files):
- 🏗️ initial implementation
- 🔨 feature changes
- 🐛 bugfix
- ✨ formatting/linting only
- ✅ feature complete with E2E tests
- ⚙️ config changes
- 💽 DB schema/migrations

## Long-Running Code Pattern

Structure as: `init()` → `continue(id)` → `cleanup(id)`
- Keep state serializable
- Use descriptive IDs (runId, taskId)
- Handle rate limits, timeouts, retries at system boundaries

## MCP: Headless vs UI

This template supports two MCP tool styles:

- **Headless tools** (default) - sync wrapper, no `Context`, returns the
  Pydantic output model so FastMCP derives `outputSchema`. The CLI/API/MCP
  transports share identical behavior. Use this for any tool the LLM should
  call autonomously without UI affordances.
- **Enhanced tools** (opt-in via `@enhance` in `mcp_server/enhancers/`) - async
  wrapper with `Context`, may elicit user input mid-call, attach images/audio,
  or render an MCP App (iframe dashboard). MCP-only - never affects CLI/API
  consumers of the same service. The pure service stays untouched in
  `services/`.

If a tool just returns data, leave it headless. Reach for an enhancer only
when the MCP transport needs something the spec offers and other transports
don't.

### Adding an enhancer

```python
from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool

@enhance("my_service", fallback="headless")
async def my_enhanced(tool: EnhancedTool[MyInput, MyOutput]) -> MyOutput:
    result = tool.call()
    if tool.can_elicit:
        ...  # await tool.elicit(...)
    if tool.can_show_app:
        tool.send_app("ui://mymcp/my_dashboard")
    return result
```

Then add `import mcp_server.enhancers.my_service  # noqa: F401` to
`_register_tools()` in `mcp_server/server.py`.

### MCP Apps (iframe dashboards)

Apps live in `mcp_server/apps/<name>/`:
- React + Vite + `vite-plugin-singlefile` (always bun, never npm)
- `dist/mcp-app.html` is **committed** so the template works without Node
- `make build_apps` rebuilds the bundle (developer-only; not part of CI)
- `make dev_host` runs the upstream `@modelcontextprotocol/ext-apps` basic-host
  for manual smoke testing

When adding a new app, also add an entry to `[tool.hatch.build.targets.wheel]
force-include` in `pyproject.toml` so the HTML ships in the wheel.

See `mcp_server/MCP_UI_ARCHITECTURE.md` for design rationale and
`mcp_server/MCP_UI_EDGE_CASES.md` for the edge-case spec.

### MCP testing (two tiers)

- **Fast tier (default CI, pytest):** unit tests for the enhancer machinery
  plus `tests/test_mcp_e2e.py`, which runs a full MCP session over the
  streamable-HTTP mount and asserts the wire format (outputSchema,
  `_meta.ui`, CallToolResult assembly). No Node needed.
- **Conformance tier:** `make mcp_conformance` (MCPJam protocol + apps
  suites, CI-gated, needs Node). OAuth conformance is manual - see
  `docs/content/docs/mcp/oauth.mdx`.
- App frontends: vitest per app (`bun run test` in `mcp_server/apps/<name>/`,
  not in CI); each app has an `appContract.test.ts` pinning the real
  `ext-apps` `App` surface against the `McpAppLike` mock type.
- **Rendered-iframe tier (L4, #37):** `make test_apps_e2e` drives the **real
  Goose desktop GUI** (Electron) as an MCP client and asserts the `ui://` app
  actually renders + round-trips in a real host - including a click →
  `callServerTool` → re-render scenario. Also wired as a skipped-by-default pytest
  entry (`tests/test_apps_e2e.py`, opt in with `RUN_APPS_E2E=1`) and an opt-in CI
  workflow (`.github/workflows/apps_e2e.yaml`, `workflow_dispatch` + weekly). Build
  + rationale: `.agents/skills/goose-gui-e2e/SKILL.md`. Heavy (Goose + Electron);
  **not** in `make ci`.

## Subagents

- Folder-size CI failure → spawn subagent `.claude/agents/folder-refactor-advisor.md`.

## Git Workflow
- **Protected Branch**: `main` is protected. Do not push directly to `main`. Use PRs.
- **Merge Strategy**: Squash and merge.
- **Never force push**: Do not use `git push --force` or `--force-with-lease`. If you hit a git issue, stop and ask the user for guidance.
- **Pre-commit CI gate**: Always run `make ci` before committing any changes. Ensure it passes with zero errors. Do not commit if `make ci` fails - fix all issues first, then commit.

## Deprecated

- Don't use `datetime.utcnow()` - use `datetime.now(timezone.utc)`
