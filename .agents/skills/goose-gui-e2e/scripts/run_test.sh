#!/usr/bin/env bash
# Run one scenario end-to-end: drive the Goose GUI (Playwright-Electron under
# xvfb-run) and assert the rendered ui:// iframe + tool round-trip. Exit 0 = PASS.
#   run_test.sh <scenario_name>          (resolves scenarios/<name>.json)
#   run_test.sh path/to/scenario.json
set -u
source "$(dirname "$0")/lib.sh"
arg="${1:?usage: run_test.sh <scenario_name|path.json>}"
scfile="$arg"; [ -f "$scfile" ] || scfile="$SCENARIO_DIR/${arg}.json"
[ -f "$scfile" ] || { echo "no scenario: $scfile"; exit 2; }
nm="$(basename "$scfile" .json)"
shot="$E2E_HOME/shot_${nm}.png"

# Resolve a Playwright entry with _electron (Goose's ui bundle ships one).
if [ -z "${PLAYWRIGHT:-}" ]; then
  for cand in \
    "$GOOSE_SRC/ui/desktop/node_modules/playwright/index.js" \
    "$GOOSE_SRC/ui/node_modules/playwright/index.js" \
    "$GOOSE_SRC/ui/node_modules/playwright-core/index.js" \
    "$REPO/node_modules/playwright/index.js"; do
    [ -f "$cand" ] && { PLAYWRIGHT="$cand"; break; }
  done
fi
export PLAYWRIGHT="${PLAYWRIGHT:-playwright}"

echo "==================== SCENARIO: $nm ===================="
echo "playwright: $PLAYWRIGHT"

# Optional per-scenario data seeding: a "seed" key naming a script in this dir
# runs before the drive, so scenarios that consume state (e.g. the pdf signing
# ceremony, which moves its document to a terminal 'signed') stay
# self-contained and re-runnable in any order.
seed_script="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('seed',''))" "$scfile")"
if [ -n "$seed_script" ]; then
  echo "---- seed: $seed_script ----"
  (cd "$REPO" && uv run python "$SCRIPT_DIR/$seed_script") >/dev/null || {
    echo "==================== $nm: FAIL (seed script failed) ===================="
    exit 1
  }
fi
# Fresh chat state per drive: accumulated Goose sessions eventually make the
# desktop boot into a resume screen instead of a new chat (observed after
# ~10 runs), leaving the prompt undelivered. Wiping is safe here - this is a
# dedicated e2e environment, and the sessions are throwaway mock chats.
# Opt out with E2E_KEEP_GOOSE_SESSIONS=1 when debugging a prior session.
if [ "${E2E_KEEP_GOOSE_SESSIONS:-}" != "1" ]; then
  rm -rf "$HOME/.local/share/goose/sessions/"* 2>/dev/null || true
fi

# Snapshot the tool-call-log size BEFORE driving. If the drive produces no new
# entries, the oracle must not grade stale ones (the false-PASS hole).
before="$(python3 "$SCRIPT_DIR/mcp_probe.py" calls_count 2>/dev/null || echo 0)"

echo "---- drive GUI (Playwright-Electron) ----"
xvfb-run -a --server-args="-screen 0 1500x1000x24" \
  timeout 200 node "$SCRIPT_DIR/pw_scenario.mjs" "$scfile" "$shot" 2>&1 | sed 's/^/  /'
drive_rc=${PIPESTATUS[0]}  # node's status, NOT sed's (a failed drive must fail the test)

if [ "$drive_rc" -ne 0 ]; then
  echo "---- drive FAILED (rc=$drive_rc) - not asserting ----"
  echo "shot: $shot"
  echo "==================== $nm: FAIL (drive did not complete) ===================="
  exit 1
fi

echo "---- assert (rendered iframe + tool round-trip) ----"
python3 "$SCRIPT_DIR/mcp_probe.py" assert "$scfile" "$before"
rc=$?
echo "shot: $shot"
echo "==================== $nm: $([ $rc -eq 0 ] && echo PASS || echo FAIL) ===================="
exit $rc
