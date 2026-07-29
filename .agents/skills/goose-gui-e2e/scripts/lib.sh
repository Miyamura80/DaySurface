#!/usr/bin/env bash
# Shared helpers + paths for the Goose-GUI e2e harness (DaySurface edition).
# Ported from Edison-Watch's goose-gui-e2e skill; adapted to test THIS repo's
# MCP Apps (the ui:// iframe dashboards) rendered inside the real Goose desktop.
#
# Encodes the hard-won sandbox lessons so callers never re-hit them:
#   - NEVER an unbounded curl (the 1-hour-hang class): every wait is bounded.
#   - Sleep-free HTTP readiness via `curl --retry` (foreground `sleep` is flaky here).
#   - Detached services via `setsid ... & disown` so they survive across tool calls.
#
# Paths are parameterized around two roots so this is portable to any session:
#   SCRIPT_DIR : where these scripts live (the skill's scripts/ dir)
#   E2E_HOME   : working dir for the heavy/ephemeral stuff (goose build, logs, shots,
#                the e2e SQLite DB). Default: $HOME/goose-e2e - override with `export E2E_HOME=...`.
# The Goose build under $E2E_HOME/goose_src is repo-agnostic and shared with any
# other goose-gui-e2e harness on the machine (e.g. Edison's) - setup.sh is idempotent.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${E2E_HOME:=$HOME/goose-e2e}"
mkdir -p "$E2E_HOME"

# The repo under test (DaySurface). Two levels up from scripts/ is the skill
# dir; the repo root is wherever the skill is checked out. Resolve it robustly.
: "${REPO:=$(cd "$SCRIPT_DIR/../../../.." && pwd)}"

# derived paths (Goose build shared with any other goose-gui-e2e harness)
: "${GOOSE_SRC:=$E2E_HOME/goose_src}"
: "${DESK:=$GOOSE_SRC/ui/desktop}"
: "${ELECTRON_BIN:=$GOOSE_SRC/ui/node_modules/electron/dist/electron}"
: "${GOOSE_BINARY:=$GOOSE_SRC/target/debug/goose}"
: "${SCENARIO_DIR:=$SCRIPT_DIR/scenarios}"
: "${CURRENT_SCENARIO:=$E2E_HOME/current_scenario.json}"
: "${TOOLCALL_LOG:=$E2E_HOME/toolcalls.jsonl}"

# e2e SQLite DB for the DaySurface server + the fixed user the API key belongs to
: "${E2E_DB:=$E2E_HOME/daysurface_e2e.db}"
: "${BACKEND_DB_URI:=sqlite:///$E2E_DB}"
: "${E2E_USER_ID:=e2e-user}"
: "${API_KEY_FILE:=$E2E_HOME/api_key.txt}"

# ports / hosts. NB: these leak into `uv run` for the DaySurface server + seed, and
# pydantic-settings is case-insensitive - so an env var named SERVER/HOST/PORT/GMAIL
# would be misread as a config field and crash boot. Keep the SRV_ prefix.
: "${VITE_PORT:=5173}"       # Goose desktop dev renderer (Electron loads this)
: "${MOCK_PORT:=8410}"       # scenario-engine mock LLM (OpenAI-compatible)
: "${SRV_PORT:=8080}"        # DaySurface's FastAPI + /mcp mount (daysurface-serve default)
: "${SRV_URL:=http://127.0.0.1:$SRV_PORT}"
: "${MCP_URL:=$SRV_URL/mcp}"

export SCRIPT_DIR E2E_HOME REPO GOOSE_SRC DESK ELECTRON_BIN GOOSE_BINARY \
       SCENARIO_DIR CURRENT_SCENARIO TOOLCALL_LOG E2E_DB BACKEND_DB_URI E2E_USER_ID \
       API_KEY_FILE VITE_PORT MOCK_PORT SRV_PORT SRV_URL MCP_URL

log(){ echo "[$(printf '%(%H:%M:%S)T' -1)] $*"; }

# wait_http URL [tries] [delay] - sleep-free bounded readiness poll (curl does the waiting)
wait_http(){ curl -sf --retry "${2:-40}" --retry-delay "${3:-1}" --retry-connrefused --retry-all-errors --max-time 5 -o /dev/null "$1"; }
# is_up_http URL - single bounded check (up == not 000)
is_up_http(){ [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$1" 2>/dev/null)" != "000" ]; }
# start_detached NAME "CMD_STRING" - long-lived service surviving the calling shell.
# Put `exec` before the final binary in CMD (e.g. "cd X && exec foo"); never `exec` a `cd`.
start_detached(){ setsid bash -c "$2" >"$E2E_HOME/${1}.log" 2>&1 & disown; log "started $1 (pid $!)"; }
