#!/usr/bin/env python3
"""Assertion oracle for the MCP-App e2e harness.

Analogue of Edison's edison_api.py, but DaySurface has no policy/sessions API,
so the verdict comes from two INDEPENDENT records - neither of which the mock LLM
can fake:
  1. the rendered ui:// iframe  (pw_result_<name>.json, written by Playwright from
     the real Goose DOM) - proves the host rendered the app the server produced;
  2. the tool-call log          ($TOOLCALL_LOG) - the calls Goose actually issued to
     /mcp and the results the server actually returned - proves the round-trip.

Usage:
  python3 mcp_probe.py calls_count                     # current tool-call-log line count (pre-drive snapshot)
  python3 mcp_probe.py assert <scenario.json> [before] # exit 0 PASS / 1 FAIL
"""

import json
import os
import sys

E2E_HOME = os.environ.get("E2E_HOME", os.path.expanduser("~/goose-e2e"))
TOOLCALL_LOG = os.environ.get("TOOLCALL_LOG") or os.path.join(
    E2E_HOME, "toolcalls.jsonl"
)


def _log_lines():
    try:
        with open(TOOLCALL_LOG) as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def calls_count() -> int:
    return len(_log_lines())


def assert_scenario(scenario_path: str, before: int = 0) -> int:
    with open(scenario_path) as fh:
        sc = json.load(fh)
    name = os.path.basename(scenario_path).replace(".json", "")
    exp = sc.get("expect", {})
    fails = []

    # --- record 1: the tool-call log (only lines produced by THIS run) ---
    new = _log_lines()[before:]
    if not new:
        print(
            "FAIL: no new tool-call-log entries for this run; GUI drive produced nothing"
        )
        return 1
    calls = [e for e in new if e.get("event") == "call"]
    got_result = any(e.get("event") == "result" for e in new)
    want_tool = exp.get("tool_called")
    if want_tool and not any(want_tool in (c.get("tool") or "") for c in calls):
        fails.append(
            f"expected tool '{want_tool}' called; calls = {[c.get('tool') for c in calls]}"
        )
    if exp.get("round_trip", True) and not got_result:
        fails.append(
            "expected a tool result to return from /mcp (round-trip); none observed"
        )

    # --- record 2: the rendered iframe (Playwright's DOM readout) ---
    pw_path = os.path.join(E2E_HOME, f"pw_result_{name}.json")
    try:
        with open(pw_path) as fh:
            pw = json.load(fh)
    except OSError:
        pw = {}
    if exp.get("app_rendered", True) and not pw.get("rendered"):
        fails.append(
            f"expected ui:// app rendered; pw rendered={pw.get('rendered')} "
            f"matched={pw.get('matched')} missing={pw.get('missing')} frames={pw.get('frames')}"
        )

    # --- scenario 2: user-initiated click -> callServerTool -> re-render ---
    # The iframe's callServerTool bypasses the mock LLM, so this round-trip cannot
    # show up in the tool-call log; the re-rendered DOM is the only proof, and only
    # the server's real response can produce it.
    if exp.get("interaction_rendered") and not pw.get("interacted"):
        # Carry the input-plumbing diagnostics into the verdict. Without them a
        # gated submit control (fill never reached the app's state, or the app
        # remounted and reset it) is indistinguishable from a server that never
        # answered - both just read as "did not re-render". See issue #28.
        fails.append(
            f"expected in-iframe interaction to re-render; pw interacted={pw.get('interacted')} "
            f"matched={pw.get('interact_matched')} missing={pw.get('interact_missing')} "
            f"fill={pw.get('fill_diag')} click_enabled={pw.get('click_enabled')}"
        )

    print(
        f"scenario {name}: tool_calls={[c.get('tool') for c in calls]} result_returned={got_result} "
        f"app_rendered={pw.get('rendered')} matched={pw.get('matched')} "
        f"interacted={pw.get('interacted')} interact_matched={pw.get('interact_matched')} "
        f"app={pw.get('app_uri')}"
    )
    if fails:
        for f in fails:
            print("  FAIL:", f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "calls_count"
    if cmd == "calls_count":
        print(calls_count())
    elif cmd == "assert":
        before = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 0
        sys.exit(assert_scenario(sys.argv[2], before))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)
