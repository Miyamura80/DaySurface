#!/usr/bin/env bash
# Idempotent bring-up of the persistent e2e stack for THIS template:
#   template server (FastAPI + /mcp mount) + mock LLM + Goose vite renderer.
# Xvfb + Electron are NOT started here - xvfb-run + Playwright own them per test.
# Safe to re-run: only starts what's down; recovers idle-reaped services.
set -u
source "$(dirname "$0")/lib.sh"

# 1) seed the e2e DB + API key (once; key cached in $API_KEY_FILE)
if [ ! -s "$API_KEY_FILE" ]; then
  log "seeding e2e DB + API key (uv run python seed.py)"
  ( cd "$REPO" && DEV_ENV=dev BACKEND_DB_URI="$BACKEND_DB_URI" E2E_USER_ID="$E2E_USER_ID" \
      uv run python "$SCRIPT_DIR/seed.py" ) | tail -1 > "$API_KEY_FILE" \
    || { log "FATAL seed"; rm -f "$API_KEY_FILE"; exit 1; }
  [ -s "$API_KEY_FILE" ] || { log "FATAL: seed produced no key"; exit 1; }
fi
API_KEY="$(cat "$API_KEY_FILE")"; export API_KEY
log "API key: ${API_KEY:0:12}..."

# 2) template server (FastAPI + FastMCP /mcp mount). SQLite persists on disk.
# GMAIL_PUBSUB_TOPIC is a real config field (common/global_config.py) - setting it
# flips push_available true so the Settings app renders its "Add endpoint" control,
# which the settings_subscribe scenario clicks. No Pub/Sub is ever contacted (there
# is no connected Gmail account); it only ungates the webhook UI.
# GMAIL_FAKE_BACKEND=1 makes _get_gmail_client serve fixture threads instead of
# hitting Google, so the gmail_thread_render scenario can render the reader iframe
# with no linked account / OAuth / network. Hard-refused under DEV_ENV=prod; here
# DEV_ENV=dev so it's active. No Gmail tool ever reaches Google in this stack.
if ! is_up_http "$SRV_URL/health"; then
  log "template server down -> starting daysurface-serve"
  start_detached mcp-server \
    "cd $REPO && exec env DEV_ENV=dev BACKEND_DB_URI=$BACKEND_DB_URI SENTRY_DSN= GMAIL_PUBSUB_TOPIC=projects/mcp-e2e/topics/gmail-e2e GMAIL_FAKE_BACKEND=1 uv run daysurface-serve"
  wait_http "$SRV_URL/health" 90 1 && log "server healthy" || { log "FATAL server"; tail -12 "$E2E_HOME/mcp-server.log"; exit 1; }
fi

# 3) mock LLM (scenario engine, OpenAI-compatible)
if ! is_up_http "http://127.0.0.1:$MOCK_PORT/v1/models"; then
  log "mock down -> starting"
  start_detached mock \
    "exec env SCENARIO_FILE=$CURRENT_SCENARIO TOOLCALL_LOG=$TOOLCALL_LOG MOCK_PORT=$MOCK_PORT python3 $SCRIPT_DIR/mock_llm.py"
  wait_http "http://127.0.0.1:$MOCK_PORT/v1/models" 20 1 && log "mock up" || { log "FATAL mock"; exit 1; }
fi

# 4) Vite renderer (serves the real Goose desktop UI for Electron to load in dev)
if ! is_up_http "http://127.0.0.1:$VITE_PORT/"; then
  log "vite down -> starting"
  start_detached vite "cd $DESK && exec ../node_modules/.bin/vite --config vite.renderer.config.mts --port $VITE_PORT --strictPort --host 127.0.0.1"
  wait_http "http://127.0.0.1:$VITE_PORT/" 40 1 && log "vite up" || { log "FATAL vite"; tail -5 "$E2E_HOME/vite.log"; exit 1; }
fi

# point Goose at the /mcp mount with the mock provider (default mode)
bash "$SCRIPT_DIR/configure_goose.sh" mock >/dev/null

log "STACK READY"
printf 'server:%s mcp:%s mock:%s vite:%s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 $SRV_URL/health)" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 $MCP_URL)" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:$MOCK_PORT/v1/models)" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:$VITE_PORT/)"
