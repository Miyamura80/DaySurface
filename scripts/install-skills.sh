#!/usr/bin/env bash
# Downloads the official gmail-mcp agent skill from the MCP-Template repository
# into .claude/skills/ (the same skill that is self-published on skills.sh).
set -euo pipefail

REPO="Miyamura80/MCP-Template"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

dir=".claude/skills/gmail-mcp"
mkdir -p "${dir}"
echo "Downloading gmail-mcp skill..."
curl -fsSL -o "${dir}/SKILL.md" "${BASE_URL}/skills/gmail-mcp/SKILL.md"

echo "Installed gmail-mcp skill into .claude/skills/gmail-mcp/"
