#!/usr/bin/env bash
# Teardown. Default: stop ephemeral test services, leaving the seeded DB + API
# key on disk for fast re-runs. --reset also drops the e2e DB + cached key.
#   down.sh            # stop server/mock/vite/electron/xvfb
#   down.sh --reset    # also delete the e2e SQLite DB + cached API key
set -u
source "$(dirname "$0")/lib.sh"
# NB: match vite by its config file, not 'node_modules/.bin/vite': the .bin shim
# re-execs as `node .../vite/bin/vite.js`, whose cmdline has no '.bin/vite' - so
# that pattern would miss the live process and leave port 5173 held.
for p in 'dist/electron' 'xvfb-run' 'Xvfb' 'vite.renderer.config.mts' 'mock_llm' 'mymcp-serve'; do
  pkill -9 -f "$p" 2>/dev/null || true
done
log "stopped test services (server/mock/vite/electron/xvfb)"
if [ "${1:-}" = "--reset" ]; then
  rm -f "$E2E_DB" "$API_KEY_FILE" "$TOOLCALL_LOG"
  log "reset: dropped e2e DB, cached API key, tool-call log"
fi
echo "teardown complete"
