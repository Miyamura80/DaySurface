#!/usr/bin/env bash
# Downloads the official daysurface agent skill from the DaySurface repository
# into .claude/skills/ (the same skill that is self-published on skills.sh).
set -euo pipefail

REPO="Miyamura80/DaySurface"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

dir=".claude/skills/daysurface"
mkdir -p "${dir}"
echo "Downloading daysurface skill..."
curl -fsSL -o "${dir}/SKILL.md" "${BASE_URL}/skills/daysurface/SKILL.md"

echo "Installed daysurface skill into .claude/skills/daysurface/"
