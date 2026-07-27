# Use bash for recipes; several targets rely on bash-only features
# (e.g. `read -d ''`) that fail under dash, the default /bin/sh on Debian/Ubuntu.
SHELL := bash

# ANSI color codes
GREEN=\033[0;32m
YELLOW=\033[0;33m
RED=\033[0;31m
BLUE=\033[0;34m
RESET=\033[0m

PYTHON=uv run
TEST=uv run python -m pytest
PROJECT_ROOT=.

# Minimum uv version required by this repo. `[tool.uv]` in pyproject.toml uses a
# relative `exclude-newer` duration that only uv >= 0.9.17 can parse; an older uv
# silently discards the whole table (guard included) and rewrites uv.lock. See #196.
MIN_UV_VERSION=0.9.17

.DEFAULT_GOAL := help

########################################################
# Help
########################################################

### Help
.PHONY: help docs
help: ## Show this help message
	@echo "$(BLUE)Available Make Targets$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "; category=""} \
		/^### / {category = substr($$0, 5); next} \
		/^[a-zA-Z_-]+:.*?## / { \
			if (category != last_category) { \
				if (last_category != "") print ""; \
				print "$(GREEN)" category ":$(RESET)"; \
				last_category = category; \
			} \
			printf "  $(YELLOW)%-23s$(RESET) %s\n", $$1, $$2 \
		}' $(MAKEFILE_LIST)

########################################################
# Check dependencies
########################################################

check_uv:
	@echo "$(YELLOW)🔍Checking uv version...$(RESET)"
	@if ! command -v uv > /dev/null 2>&1; then \
		echo "$(RED)uv is not installed. Please install uv before proceeding.$(RESET)"; \
		exit 1; \
	fi
	@uv --version
	@UV_VERSION=$$(uv --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1); \
	if [ -z "$$UV_VERSION" ]; then \
		echo "$(RED)Could not determine uv version from 'uv --version'.$(RESET)"; \
		exit 1; \
	fi; \
	if [ "$$(printf '%s\n' "$(MIN_UV_VERSION)" "$$UV_VERSION" | sort -V | head -n1)" != "$(MIN_UV_VERSION)" ]; then \
		echo "$(RED)uv $$UV_VERSION is too old - this repo needs uv >= $(MIN_UV_VERSION) (relative exclude-newer in [tool.uv]).$(RESET)"; \
		echo "$(RED)Fix: run .claude/hooks/session-start.sh, or 'uv tool install --force uv>=$(MIN_UV_VERSION)'.$(RESET)"; \
		exit 1; \
	fi

check_jq:
	@echo "$(YELLOW)🔍Checking jq version...$(RESET)"
	@if ! command -v jq > /dev/null 2>&1; then \
		echo "$(RED)jq is not installed. Please install jq before proceeding.$(RESET)"; \
		echo "$(RED)brew install jq$(RESET)"; \
		exit 1; \
	else \
		jq --version; \
	fi

########################################################
# Python dependency-related
########################################################

view_python_venv_size:
	@echo "$(YELLOW)🔍Checking python venv size...$(RESET)"
	@PYTHON_VERSION=$$(cat .python-version | cut -d. -f1,2) && \
	cd .venv/lib/python$$PYTHON_VERSION/site-packages && du -sh . && cd ../../../
	@echo "$(GREEN)Python venv size check completed.$(RESET)"

view_python_venv_size_by_libraries:
	@echo "$(YELLOW)🔍Checking python venv size by libraries...$(RESET)"
	@PYTHON_VERSION=$$(cat .python-version | cut -d. -f1,2) && \
	cd .venv/lib/python$$PYTHON_VERSION/site-packages && du -sh * | sort -h && cd ../../../
	@echo "$(GREEN)Python venv size by libraries check completed.$(RESET)"

########################################################
# Run Main Application
########################################################

### Running
all: check_uv ## Sync dependencies
	@uv sync
	@echo "$(GREEN)✅ Dependencies synced.$(RESET)"

cli: check_uv ## Run the CLI (pass ARGS="..." for arguments)
	@$(PYTHON) -m src.cli.app $(ARGS)

docs: ## Run docs with bun
	@echo "$(GREEN)📚Running docs...$(RESET)"
	@cd docs && bun run dev
	@echo "$(GREEN)✅ Docs run completed.$(RESET)"

api: check_uv ## Run authenticated API server
	@echo "$(GREEN)🌐 Starting API server...$(RESET)"
	@$(PYTHON) daysurface-serve

mcp: check_uv ## Run MCP server locally (stdio)
	@$(PYTHON) daysurface-mcp

mcp_inspect: check_uv ## Run MCP server with inspector for debugging
	@$(PYTHON) mcp dev mcp_server/server.py

build_apps: ## Build all MCP App frontends to dist/mcp-app.html (requires bun)
	@command -v bun >/dev/null 2>&1 || { echo "$(RED)bun is not installed. Install from https://bun.sh$(RESET)"; exit 1; }
	@for dir in mcp_server/apps/*/; do \
		if [ -f "$$dir/package.json" ]; then \
			echo "$(YELLOW)📦 Building $$dir$(RESET)"; \
			(cd "$$dir" && bun install --silent && bun run build); \
		fi; \
	done
	@echo "$(GREEN)✅ All MCP Apps built.$(RESET)"

dev_host: ## Run upstream @modelcontextprotocol/ext-apps basic-host for manual smoke testing
	@command -v bun >/dev/null 2>&1 || { echo "$(RED)bun is not installed. Install from https://bun.sh$(RESET)"; exit 1; }
	@echo "$(YELLOW)Cloning ext-apps basic-host (one-time)...$(RESET)"
	@if [ ! -d /tmp/ext-apps ]; then git clone --depth 1 https://github.com/modelcontextprotocol/ext-apps.git /tmp/ext-apps; fi
	@cd /tmp/ext-apps/examples/basic-host && bun install --silent && bun start

.PHONY: preview_app preview_smoke
APP ?= gmail_inbox
preview_app: ## Build a standalone fixture-driven preview of an MCP App (APP=gmail_inbox)
	@command -v bun >/dev/null 2>&1 || { echo "$(RED)bun is not installed. Install from https://bun.sh$(RESET)"; exit 1; }
	@echo "$(YELLOW)🖼  Building $(APP) fixture preview...$(RESET)"
	@cd mcp_server/dev_preview && bun install --silent && APP=$(APP) bun run build.mjs

preview_smoke: ## Headless Playwright check that an MCP App renders from fixtures (APP=gmail_inbox)
	@command -v bun >/dev/null 2>&1 || { echo "$(RED)bun is not installed. Install from https://bun.sh$(RESET)"; exit 1; }
	@echo "$(YELLOW)🧪 Smoke-testing $(APP) fixture preview...$(RESET)"
	@cd mcp_server/dev_preview && bun install --silent && APP=$(APP) bun run smoke.mjs

mcp_conformance: check_uv ## Run MCPJam apps + protocol conformance against the local /mcp server (requires node)
	@command -v npx >/dev/null 2>&1 || { echo "$(RED)npx (Node.js) is not installed. Install from https://nodejs.org$(RESET)"; exit 1; }
	@echo "$(YELLOW)🔌 Running MCPJam conformance...$(RESET)"
	@uv run python scripts/mcp_conformance.py
	@echo "$(GREEN)✅ MCP conformance passed.$(RESET)"

GOOSE_E2E := .agents/skills/goose-gui-e2e/scripts
test_apps_e2e: ## L4 MCP-App e2e: drive the real Goose desktop GUI as an MCP client (needs one-time setup; NOT in make ci)
	@test -x "$$HOME/goose-e2e/goose_src/target/debug/goose" || { \
		echo "$(YELLOW)⚠️  Goose not built yet. Run once: bash $(GOOSE_E2E)/setup.sh$(RESET)"; \
		echo "$(YELLOW)   (needs registry.npmmirror.com on the network allowlist; ~6-8 min)$(RESET)"; exit 1; }
	@echo "$(YELLOW)🪿 Running Goose-GUI MCP-App e2e (real Electron host)...$(RESET)"
	@bash $(GOOSE_E2E)/up.sh
	@bash $(GOOSE_E2E)/run_all.sh
	@echo "$(GREEN)✅ MCP-App e2e passed.$(RESET)"

gen_tool_surface: check_uv ## Snapshot the @service registry to the landing-page tool-surface JSON
	@echo "$(YELLOW)🛠  Exporting tool surface...$(RESET)"
	@uv run python scripts/export_tool_surface.py
	@echo "$(GREEN)✅ Tool surface exported.$(RESET)"

########################################################
# Run Tests
########################################################

TEST_TARGETS = tests/

### Testing
test: check_uv ## Run all pytest tests
	@echo "$(GREEN)🧪Running Target Tests...$(RESET)"
	$(TEST) $(TEST_TARGETS)
	@echo "$(GREEN)✅Target Tests Passed.$(RESET)"

test_fast: check_uv ## Run fast tests (exclude slow/nondeterministic)
	@echo "$(GREEN)🧪Running Fast Tests...$(RESET)"
	$(TEST) -m "not slow and not nondeterministic" $(TEST_TARGETS)
	@echo "$(GREEN)✅Fast Tests Passed.$(RESET)"

test_slow: check_uv ## Run slow tests only
	@echo "$(GREEN)🧪Running Slow Tests...$(RESET)"
	@$(TEST) -m "slow" $(TEST_TARGETS); \
	status=$$?; \
	if [ $$status -eq 5 ]; then \
		echo "$(YELLOW)⚠️ No slow tests collected.$(RESET)"; \
		exit 0; \
	fi; \
	exit $$status

test_nondeterministic: check_uv ## Run nondeterministic tests only
	@echo "$(GREEN)🧪Running Nondeterministic Tests...$(RESET)"
	@$(TEST) -m "nondeterministic" $(TEST_TARGETS); \
	status=$$?; \
	if [ $$status -eq 5 ]; then \
		echo "$(YELLOW)⚠️ No nondeterministic tests collected.$(RESET)"; \
		exit 0; \
	fi; \
	exit $$status

test_flaky: check_uv ## Repeat fast tests to detect flaky tests
	@echo "$(GREEN)🧪Running Flaky Test Detection...$(RESET)"
	$(TEST) --count 2 -m "not slow and not nondeterministic" $(TEST_TARGETS)
	@echo "$(GREEN)✅Flaky Test Detection Passed.$(RESET)"


########################################################
# Cleaning
########################################################

# Linter will ignore these directories
IGNORE_LINT_DIRS = .venv venv
LINE_LENGTH = 88
FIND_PRUNE = $(foreach dir,$(IGNORE_LINT_DIRS),-path "./$(dir)" -o) -false

### Code Quality
install_tools: check_uv ## Install linting/formatting tools
	@echo "$(YELLOW)🔧Installing tools...$(RESET)"
	@uv tool install ruff --force
	@uv tool install import-linter --force
	@uv tool install ty --force
	@uv tool install vulture --force
	@echo "$(GREEN)✅Tools installed.$(RESET)"

fmt: install_tools check_jq ## Format code with ruff and jq
	@echo "$(YELLOW)✨Formatting project with Ruff...$(RESET)"
	@uv tool run ruff format
	@echo "$(YELLOW)✨Formatting JSONs with jq...$(RESET)"
	@count=0; \
	while IFS= read -r -d '' file; do \
		if jq . "$$file" > "$$file.tmp" 2>/dev/null && mv "$$file.tmp" "$$file"; then \
			count=$$((count + 1)); \
		else \
			rm -f "$$file.tmp"; \
		fi; \
	done < <(find . \( $(FIND_PRUNE) \) -prune -o -type f -name '*.json' -print0); \
	echo "$(BLUE)$$count JSON file(s)$(RESET) formatted."; \
	echo "$(GREEN)✅Formatting completed.$(RESET)"

ruff: install_tools ## Run ruff linter
	@echo "$(YELLOW)🔍Running ruff...$(RESET)"
	@uv tool run ruff check
	@echo "$(GREEN)✅Ruff completed.$(RESET)"

complexity: install_tools ## Check cyclomatic complexity
	@echo "$(YELLOW)🔍Checking cyclomatic complexity...$(RESET)"
	@uv tool run ruff check --select C901
	@echo "$(GREEN)✅Complexity check completed.$(RESET)"

tech_debt: install_tools ## Check TODO/FIXME markers
	@echo "$(YELLOW)🔍Checking tech debt markers...$(RESET)"
	@uv tool run ruff check --select FIX
	@echo "$(GREEN)✅Tech debt check completed.$(RESET)"

duplicate_code: check_uv ## Detect duplicate code blocks
	@echo "$(YELLOW)🔍Checking duplicate code...$(RESET)"
	@uv run python -m pylint --disable=all --enable=R0801 src common utils
	@echo "$(GREEN)✅Duplicate code check completed.$(RESET)"

vulture: install_tools ## Find dead code with vulture
	@echo "$(YELLOW)🔍Running Vulture...$(RESET)"
	@uv tool run vulture .
	@echo "$(GREEN)✅Vulture completed.$(RESET)"

import_lint: install_tools ## Enforce module boundaries with import-linter
	@echo "$(YELLOW)🔍Running Import Linter...$(RESET)"
	@uv tool run --from import-linter lint-imports
	@echo "$(GREEN)✅Import Linter completed.$(RESET)"

ty: install_tools ## Run type checker
	@echo "$(YELLOW)🔍Running Typer...$(RESET)"
	@uv run ty check
	@echo "$(GREEN)✅Typer completed.$(RESET)"

docs_lint: ## Lint docs links
	@echo "$(YELLOW)🔍Linting docs links...$(RESET)"
	@cd docs && bun install --frozen-lockfile && bun run lint:links
	@echo "$(GREEN)✅Docs linting completed.$(RESET)"

lint_links: ## Lint all markdown links using pytest-check-links
	@echo "$(YELLOW)🔍Linting all markdown links with pytest-check-links...$(RESET)"
	@uv run --no-project --with "pytest<9.1" --with "pytest-check-links" python scripts/lint_links.py
	@echo "$(GREEN)✅Link linting completed.$(RESET)"

agents_validate: ## Validate AGENTS.md content
	@echo "$(YELLOW)🔍Validating AGENTS.md...$(RESET)"
	@$(PYTHON) scripts/validate_agents_md.py
	@echo "$(GREEN)✅AGENTS.md validation completed.$(RESET)"

check_deps: install_tools ## Check for unused dependencies
	@echo "$(YELLOW)🔍Checking unused dependencies...$(RESET)"
	@uv run deptry .
	@echo "$(GREEN)✅Dependency check completed.$(RESET)"

file_len_check: check_uv ## Check source files (.py/.ts/.tsx) don't exceed max line count
	@echo "$(YELLOW)🔍Checking file lengths...$(RESET)"
	@uv run python scripts/check_file_length.py
	@echo "$(GREEN)✅File length check completed.$(RESET)"

blind_except_check: check_uv ## Check every `# noqa: BLE001` has a justification comment
	@echo "$(YELLOW)🔍Checking blind-except justifications...$(RESET)"
	@uv run python scripts/check_blind_except_justification.py
	@echo "$(GREEN)✅Blind-except justification check completed.$(RESET)"

ci: ruff vulture import_lint ty docs_lint check_deps file_len_check blind_except_check ## Run all CI checks (ruff, vulture, import_lint, ty, docs_lint, check_deps, file_len_check, blind_except_check)
	@echo "$(GREEN)✅CI checks completed.$(RESET)"

.PHONY: sync-agent-config
sync-agent-config: check_uv ## Sync Claude <-> Codex skills & subagents (regenerates symlinks and .codex/agents/*.toml)
	@uv run scripts/sync_agent_config.py

.PHONY: sync-skills
sync-skills: check_uv ## Mirror skills/ into landing-page/.well-known and refresh index.json digests
	@uv run scripts/sync_skills.py

########################################################
# Database
########################################################

### Database
db_migrate: check_uv ## Run Alembic migrations (upgrade head)
	@echo "$(YELLOW)🗄️  Running database migrations...$(RESET)"
	@$(PYTHON) -m alembic upgrade head
	@echo "$(GREEN)✅ Migrations applied.$(RESET)"

db_revision: check_uv ## Create a new Alembic migration revision (ARGS="message")
	@echo "$(YELLOW)🗄️  Creating migration revision...$(RESET)"
	@$(PYTHON) -m alembic revision --autogenerate -m "$(ARGS)"
	@echo "$(GREEN)✅ Revision created.$(RESET)"

########################################################
# Release
########################################################

### Release
BUMP ?= patch

bump_version: ## Bump version (BUMP=patch|minor|major), commit, and tag
	@git diff --cached --quiet || { echo "$(RED)Staging area is not clean. Commit or unstage changes first.$(RESET)"; exit 1; }; \
	git diff --quiet || { echo "$(RED)Working tree is not clean. Commit or stash changes first.$(RESET)"; exit 1; }; \
	current=$$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/'); \
	major=$$(echo "$$current" | cut -d. -f1); \
	minor=$$(echo "$$current" | cut -d. -f2); \
	patch_v=$$(echo "$$current" | cut -d. -f3); \
	case "$(BUMP)" in \
		major) major=$$((major + 1)); minor=0; patch_v=0 ;; \
		minor) minor=$$((minor + 1)); patch_v=0 ;; \
		patch) patch_v=$$((patch_v + 1)) ;; \
		*) echo "$(RED)Invalid BUMP value: $(BUMP). Use patch, minor, or major.$(RESET)"; exit 1 ;; \
	esac; \
	new="$$major.$$minor.$$patch_v"; \
	echo "$(YELLOW)Bumping version: $$current -> $$new$(RESET)"; \
	sed -i.bak "s/^version = \".*\"/version = \"$$new\"/" pyproject.toml && rm -f pyproject.toml.bak && \
	git add pyproject.toml && \
	git commit -m "Release v$$new" && \
	git tag -a "v$$new" -m "Release v$$new" && \
	echo "$(GREEN)Tagged v$$new. Push with: git push origin main --follow-tags$(RESET)"

########################################################
# Dependencies
########################################################

requirements:
	@echo "$(YELLOW)🔍Checking requirements...$(RESET)"
	@cp requirements-dev.lock requirements.txt
	@echo "$(GREEN)✅Requirements checked.$(RESET)"
