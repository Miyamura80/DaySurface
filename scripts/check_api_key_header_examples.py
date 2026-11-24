"""Fail if a doc or example sends this server's API key as an `Authorization: Bearer`.

The server reads API keys only from the `X-API-KEY` header; the Bearer slot is
for WorkOS JWTs and OAuth tokens. An example that puts the key in the Bearer
slot 401s for anyone who copies it. The pattern targets the shapes that have
shipped after the Bearer scheme: literal `sk_` placeholders, shell variables
(`$TOKEN`, `$MYMCP_API_KEY`, `${X_API_KEY}`) and Python f-string fields (`{KEY}`).

Scans tracked docs and example source via `git ls-files`. Directories that
legitimately send third-party bearer tokens (`servers/`, `services/`) or
implement or test auth (`api_server/`, `tests/`) are excluded; a genuine
third-party bearer elsewhere goes in `ALLOWLIST` with a reason.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GLOBS = ("*.md", "*.mdx", "*.ts", "*.tsx", "*.astro", "*.json", "*.py")
EXCLUDED_DIRS = ("tests/", "services/", "api_server/", "servers/")

BEARER_API_KEY = re.compile(
    r"Bearer\s+(?:sk[_-]|\$\{?\w*API_KEY\b|\$\{?TOKEN\b|\{\w*KEY\})",
    re.IGNORECASE,
)

# (path, substring on the line) -> why this bearer is not our API key.
ALLOWLIST: dict[tuple[str, str], str] = {}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *GLOBS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if not p.startswith(EXCLUDED_DIRS)]


def is_allowlisted(path: str, line: str) -> bool:
    return any(p == path and s in line for p, s in ALLOWLIST)


def find_offenders(path: str, text: str) -> list[tuple[int, str]]:
    return [
        (lineno, line.strip())
        for lineno, line in enumerate(text.splitlines(), 1)
        if BEARER_API_KEY.search(line) and not is_allowlisted(path, line)
    ]


def main() -> int:
    offenders: list[str] = []
    for path in tracked_files():
        file = REPO_ROOT / path
        if not file.is_file() or file.is_symlink():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        offenders += [f"{path}:{n}: {line}" for n, line in find_offenders(path, text)]
    if offenders:
        print("API keys go in `X-API-KEY: sk_...`, not `Authorization: Bearer`:")
        print("\n".join(offenders))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
