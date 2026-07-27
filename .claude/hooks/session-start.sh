#!/bin/bash
# SessionStart hook: install project dependencies and wire up prek git hooks
# so commits in Claude Code on the web run the same checks as local clones.
#
# Scoped to remote (web) sessions: locally, CLAUDE.md already instructs
# `uv tool install prek && prek install`. Remove the CLAUDE_CODE_REMOTE guard
# below if you want this to run on local sessions too.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Ensure uv-managed tools are on PATH for this and later session commands.
export PATH="$HOME/.local/bin:$PATH"
# Persist for later commands, but only once: SessionStart fires on resume too,
# so guard against appending a duplicate line on every resume.
path_line='export PATH="$HOME/.local/bin:$PATH"'
if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qF "$path_line" "$CLAUDE_ENV_FILE" 2>/dev/null; then
  echo "$path_line" >> "$CLAUDE_ENV_FILE"
fi

# pyproject.toml requires uv >= 0.9.17 for the relative `exclude-newer`
# supply-chain quarantine. Older uv can't parse it and rewrites uv.lock on
# every invocation, which makes prek fail every commit. Upgrade from PyPI
# (uv's own self-update path hits GitHub rate limits in sandboxed networks).
REQUIRED_UV="0.9.17"
current_uv="$(uv --version 2>/dev/null | awk '{print $2}')"
if [ -z "$current_uv" ] || [ "$(printf '%s\n%s\n' "$REQUIRED_UV" "$current_uv" | sort -V | head -1)" != "$REQUIRED_UV" ]; then
  uv tool install --force "uv>=${REQUIRED_UV}"
  hash -r
fi

# Keep the lint/format toolchain on the latest stable release. Unlike uv above
# (pinned to a floor for a specific feature), ruff and ty ship fixes frequently
# and we always want the newest stable build. `uv tool install` pulls the latest
# from PyPI on first use; `uv tool upgrade` refreshes an existing install.
for tool in ruff ty; do
  if uv tool list 2>/dev/null | grep -q "^${tool} "; then
    uv tool upgrade "$tool"
  else
    uv tool install "$tool"
  fi
done
hash -r

# Sync Python dependencies (idempotent; no-op when already in sync).
uv sync

# Install prek if it isn't already available, then wire up the git hooks.
if ! command -v prek >/dev/null 2>&1; then
  uv tool install prek
fi
prek install

exit 0
