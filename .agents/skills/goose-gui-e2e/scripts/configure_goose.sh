#!/usr/bin/env bash
# Write ~/.config/goose/config.yaml + secrets.yaml for the chosen LLM mode.
# Wires THIS template's /mcp mount as a streamable-HTTP extension either way
# (that's the system under test), authenticating with the seeded API key.
#
#   configure_goose.sh mock                 # deterministic mock LLM (default, offline, CI)
#   configure_goose.sh real anthropic KEY   # real LLM (model from $GOOSE_REAL_MODEL)
#
# Mock  -> scenario `plan` drives the exact tool sequence (deterministic).
# Real  -> the LLM decides tool calls from the scenario `prompt` (realistic,
#          non-deterministic). Requires an API key AND the provider host on the
#          network allowlist. The render/round-trip assertions are unchanged.
set -u
source "$(dirname "$0")/lib.sh"
CFG_DIR="$HOME/.config/goose"; mkdir -p "$CFG_DIR"
mode="${1:-mock}"
API_KEY="${API_KEY:-$(cat "$API_KEY_FILE" 2>/dev/null)}"
[ -n "$API_KEY" ] || { echo "FATAL: no API key (run up.sh first to seed one)"; exit 1; }

write_config(){ cat > "$CFG_DIR/config.yaml" <<EOF
GOOSE_PROVIDER: $1
GOOSE_MODEL: $2
GOOSE_MODE: auto
extensions:
  mymcp:
    type: streamable_http
    name: mymcp
    uri: ${MCP_URL}
    enabled: true
    timeout: 300
    headers:
      X-API-KEY: ${API_KEY}
EOF
}

case "$mode" in
  mock)
    write_config openai gpt-4o-mini
    printf 'OPENAI_API_KEY: sk-mock\nOPENAI_BASE_URL: http://127.0.0.1:%s/v1\n' "$MOCK_PORT" > "$CFG_DIR/secrets.yaml"
    echo "configured: MOCK LLM (scenario plan drives tool calls); mymcp -> ${MCP_URL}";;
  real)
    provider="${2:?real mode needs a provider, e.g. anthropic}"; key="${3:?real mode needs an API key}"
    model="${GOOSE_REAL_MODEL:-claude-sonnet-5}"
    write_config "$provider" "$model"
    printf '%s_API_KEY: %s\n' "$(echo "$provider" | tr '[:lower:]' '[:upper:]')" "$key" > "$CFG_DIR/secrets.yaml"
    echo "configured: REAL LLM provider=$provider model=$model (agent decides tool calls)"
    echo "NOTE: needs egress to the provider host (e.g. api.anthropic.com on the allowlist)";;
  *) echo "usage: configure_goose.sh <mock|real> [provider key]"; exit 2;;
esac
