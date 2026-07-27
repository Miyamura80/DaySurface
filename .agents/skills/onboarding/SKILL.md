---
name: onboarding
description: Interview the user, inspect this template repo, run headless onboarding, and prune unused systems so a new project gets running quickly.
---

# Onboarding

Use this skill when the user wants to turn this template into a real project, especially when they invoke `/onboarding`, ask to run onboarding, or want to remove unused template systems.

`init/onboard.py` is the source of truth for every choice, default, and prune action. Read it before changing anything. The enums and `OnboardingConfig` (`ServiceSurface`, `PaymentStack`, `ExampleApp`, `OnboardingProfile`, `expanded()`) define what can be selected and how selections imply each other.

## Workflow

1. Inspect the repo before changing anything:
   - `AGENTS.md` / `CLAUDE.md`, `pyproject.toml`, `Makefile`, `README.md`
   - `init/onboard.py` - especially the config enums, `for_profile`, `expanded()` (dependency implications), and `_pruned_path_groups` (exactly which files each choice removes)
   - The surfaces themselves: service registry (`services/`), CLI (`src/cli/`), MCP server (`mcp_server/`, incl. `apps/`, `enhancers/`), API server (`api_server/`, incl. `routes/`, `auth/`, `billing/`), `db/`, `docs/`, and relevant tests
   - Systems `make onboard` does NOT manage (handle these manually - see step 7): `landing-page/`, `render.yaml`, `Dockerfile`

2. Interview the user briefly. Prefer grouped multi-select questions:
   - Project shape (`--profile`): `cli-only`, `local-mcp`, `remote-mcp`, `full-saas`, or `custom`
   - Service surfaces (`--surfaces`): `cli`, `mcp`, `http_api`, `mcp_apps`
   - Infra: auth (`--auth`, WorkOS AuthKit OAuth 2.1 resource server on `/mcp`), database (`--database`), docs (`--docs`)
   - Payments (`--payments`): `stripe_billing`, `stripe_metering`, `x402`, `mpp_stub`, `acp_stub`, or none
   - Examples (`--examples`): `gmail_google_oauth`, `agentic_payment_research_docs`, or none

3. Use `make onboard` as the source of truth. It maps `PROFILE=`, `CONFIG=`, and `DRY_RUN=1` to flags and passes everything else through `ARGS=`:
   - Always start with a dry run: `make onboard PROFILE=<profile> DRY_RUN=1`.
   - Override individual axes via `ARGS`, e.g. `make onboard PROFILE=remote-mcp DRY_RUN=1 ARGS="--surfaces cli,mcp,http_api --payments x402 --no-docs"`. Boolean axes use paired flags: `--auth/--no-auth`, `--database/--no-database`, `--docs/--no-docs`.
   - For a custom shape, write a YAML/JSON config and pass `CONFIG=<path>` (keys: `profile`, `service_surfaces`, `payments`, `examples`, `auth`, `database`, `docs`).
   - Bare `make onboard` (no headless flags) launches the interactive wizard (branding, rename, cli-name, deps, env, hooks, mcp, media, jules).
   - Only run a non-dry pass after the user confirms the resolved plan printed by the dry run.

4. Apply dependency implications before pruning (`expanded()` enforces these):
   - Stripe metering implies Stripe billing.
   - Stripe billing implies HTTP API, auth, and database.
   - x402 implies HTTP API and auth.
   - Gmail/Google OAuth example implies HTTP API, MCP, MCP Apps, auth, and DB.
   - MCP Apps imply the MCP surface; auth implies database; `full-saas` implies docs.

5. Let onboarding prune deterministically; do not hand-delete. A non-dry run removes files and also rewrites `pyproject.toml` (deps, packages, scripts, vulture, MCP Apps force-include), `.importlinter`, `api_server/server.py` route registrations, `mcp_server/_tool_factory.py` scope/quota guards, `Makefile` targets, and `.env.example` keys in the same pass. Pruning the API surface also drops `railway.json`; pruning MCP drops `server.json`, `smithery.yaml`, and the registry-publish workflow.

6. Verify the selected shape:
   - CLI: run the CLI help and one simple command.
   - API/MCP: import the server, check `/health`, and confirm `/mcp` if kept.
   - MCP behavior: run the fast MCP E2E tier (`make test`); reach for `make mcp_conformance` only if the user kept MCP and has Node.
   - Shared changes: run focused tests first, then `make ci` when scope is large.

7. Handle the systems onboarding does not touch, after confirming with the user:
   - `landing-page/` is a GmailMCP-branded Astro marketing site (privacy/terms, comparison pages, WebMCP, Server Card, llms.txt). For a real project it almost always needs rebranding or removal, which `make onboard` will not do for you.
   - `render.yaml` and `Dockerfile` are deploy configs not wired into pruning; update or remove them to match the kept surfaces.

## Guardrails

- Do not delete auth, DB, API, MCP, docs, payment code, example apps, the landing page, or deploy configs without explicit user confirmation.
- Do not push to `main`, force-push, or run destructive git commands.
- Treat MCP behavior as current-spec-sensitive; verify current docs before designing new MCP semantics.
- When editing shared skills or agents, run `make sync-agent-config`.
- Keep Gmail/Google OAuth, the agentic-payment research docs, and the landing page framed as examples/marketing, not core template infrastructure.
