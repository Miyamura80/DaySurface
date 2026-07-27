#!/usr/bin/env bash
# Run every scenario in scenarios/ (or the ones named as args) and print a summary.
#   run_all.sh                 # all scenarios/*.json
#   run_all.sh settings_render
set -u
source "$(dirname "$0")/lib.sh"
names=("$@")
if [ "${#names[@]}" -eq 0 ]; then
  for f in "$SCENARIO_DIR"/*.json; do names+=("$(basename "$f" .json)"); done
fi
pass=0; fail=0; declare -a results
for n in "${names[@]}"; do
  if bash "$SCRIPT_DIR/run_test.sh" "$n" >"$E2E_HOME/test_${n}.out" 2>&1; then
    results+=("PASS  $n"); pass=$((pass+1))
  else
    results+=("FAIL  $n  (see $E2E_HOME/test_${n}.out)"); fail=$((fail+1))
  fi
done
echo "==================== SUMMARY ===================="
printf '%s\n' "${results[@]}"
echo "-------------------------------------------------"
echo "$pass passed, $fail failed"
exit $([ $fail -eq 0 ] && echo 0 || echo 1)
