#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "pytest-check-links"]
# ///
"""Lint markdown links with exponential backoff for rate-limited URLs.

Wraps pytest-check-links, retrying the full check up to 3 times with
exponential backoff when failures are all 429 (rate-limited). Non-429
failures (404, 403, etc.) are not retried.

Domains that block automated requests outright (403 regardless of
backoff) are added to the ignore list.
"""

import os
import re
import subprocess
import sys
import time

BOT_BLOCKED_DOMAINS = [
    r"https://www\.mastercard\.com/.*",
    r"https://news\.ycombinator\.com/.*",
    # Railway template deploy links are browser-only SPA routes; they 404 for
    # non-browser HTTP clients even though they resolve fine in a browser.
    r"https://railway\.com/deploy/.*",
    # chatgpt.com sits behind Cloudflare bot protection and serves the app
    # itself to signed-in browsers only - a checker gets 403/redirect wherever
    # it runs, so this belongs here rather than in CLOUD_SANDBOX_IGNORES (which
    # would still fail open-egress CI). The one link we have is the
    # create-connector deep link in docs/content/docs/mcp/chatgpt.mdx; verify it
    # by clicking it, not by link-linting it.
    r"https://chatgpt\.com/.*",
]

# Domains we deliberately keep OFF the Claude Code cloud sandbox network
# allowlist because they are programmable endpoints (SSRF relays, RPC, package/
# tool registries, render-and-share services) that widen the data-exfiltration
# surface, not static docs. The sandbox egress proxy returns 403 for them, so we
# skip them only when running inside the cloud sandbox. Normal CI (open egress)
# still checks them for real. See `make lint_links` discussion / network policy.
CLOUD_SANDBOX_IGNORES = [
    r"https?://(www\.)?shields\.io(/|\?|$)",
    r"https?://img\.shields\.io(/|\?|$)",
    r"https?://rpc\.tempo\.xyz(/|\?|$)",
    r"https?://registry\.modelcontextprotocol\.io(/|\?|$)",
    r"https?://mcp\.daysurface\.com(/|\?|$)",
    r"https?://carbon\.now\.sh(/|\?|$)",
    r"https?://chalk\.ist(/|\?|$)",
    r"https?://facilitator\.x402\.org(/|\?|$)",
    r"https?://jules\.googleapis\.com(/|\?|$)",
    r"https?://skills\.sh(/|\?|$)",
    r"https?://banner\.godori\.dev(/|\?|$)",
    r"https?://contrib\.rocks(/|\?|$)",
    r"https?://(www\.)?render\.com(/|\?|$)",
    r"https?://(www\.)?railway\.com(/|\?|$)",
    # VS Code Marketplace is a publish surface (extension gallery API); keep it
    # off the allowlist rather than grant standing egress for one convenience
    # link to an extension page.
    r"https?://marketplace\.visualstudio\.com(/|\?|$)",
    # One-click MCP install deep links in the README's client table. Both hosts
    # are programmable surfaces an attacker could push data into and read back
    # (claude.ai is a chat product; vscode.dev is a full web IDE), so they stay
    # off the allowlist and are skipped in the sandbox only.
    r"https?://(www\.)?claude\.ai(/|\?|$)",
    r"https?://(www\.)?vscode\.dev(/|\?|$)",
    # cursor.com/install-mcp takes a base64 `config=` deep-link parameter the
    # host decodes and acts on - same relay shape as the other install links, so
    # it stays off the allowlist and is skipped in the sandbox only.
    r"https?://(www\.)?cursor\.com(/|\?|$)",
    # GitHub hosts user-writable, readable-back surfaces (gists, repo contents,
    # issue bodies) - a textbook publish-then-read exfil channel. Skip in the
    # sandbox rather than grant standing egress for repo links.
    r"https?://(www\.)?github\.com(/|\?|$)",
    # github.io is a user-publishable hosting apex (anyone can serve a site on a
    # subdomain), and the Goose install deep link in the README's client table
    # takes a `?url=` parameter the host acts on - same relay shape as
    # shields.io. Both reasons keep it off the allowlist.
    r"https?://([a-z0-9-]+\.)?github\.io(/|\?|$)",
    # Package registry (publish-then-read), same reasoning as the MCP registry.
    r"https?://docs\.pypi\.org(/|\?|$)",
    # Platform apexes carrying APIs/webhooks well beyond the one doc link.
    # (jules.googleapis.com is already listed above.)
    r"https?://jules\.google(/|\?|$)",
    r"https?://developers\.openai\.com(/|\?|$)",
    # --- Static docs/blogs: read-only GETs, no exfil channel. --------------
    # These are the one group that would be *better* served by the sandbox
    # network allowlist ("Network access -> Custom" in the environment config),
    # which would make them pass here AND in CI. They are listed here only so a
    # cloud session can commit markdown at all. Move them to the allowlist and
    # delete these four lines when convenient.
    r"https?://(docs\.)?pydantic\.dev(/|\?|$)",
    r"https?://(www\.)?fumadocs\.dev(/|\?|$)",
    r"https?://(www\.)?mcpcat\.io(/|\?|$)",
    r"https?://(www\.)?obsidiansecurity\.com(/|\?|$)",
]

# Files whose external links are deliberately not checked inside the cloud
# sandbox. The research doc cites ~20 third-party news/vendor pages; rather than
# widen the sandbox allowlist to all of them, we skip the file in the sandbox
# only. Normal CI (open egress) still link-checks it.
CLOUD_SANDBOX_IGNORE_FILES = [
    "./docs/agentic_payment_protocols_research.md",
]


def _in_claude_code_cloud() -> bool:
    """True when running inside the Claude Code cloud sandbox.

    Claude Code on the web sets ``CLAUDE_CODE_REMOTE=true`` in the session
    shell. We gate the extra ignores on this so the skip never leaks into a
    developer's local run or GitHub Actions CI (which have open egress).
    """
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


MAX_RETRIES = 3
BASE_DELAY = 5


def _build_cmd(extra_ignores: list[str]) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cov",
        "-o",
        "addopts=",
        # Markdown lives inside importable packages (e.g. mcp_server/ has an
        # __init__.py). Default prepend mode makes pytest import the package to
        # collect a .md file in it, dragging in project deps this isolated env
        # doesn't have. importlib mode collects the file without importing its
        # parent package.
        "--import-mode=importlib",
        "--check-links",
        "--check-links-ignore",
        r"http://localhost:.*",
    ]
    for pattern in extra_ignores:
        cmd.extend(["--check-links-ignore", pattern])

    find_cmd = [
        "find",
        ".",
        "-name",
        "*.md",
        "-not",
        "-path",
        "./.venv/*",
        "-not",
        "-path",
        "./.venv-test/*",
        "-not",
        "-path",
        "*/node_modules/*",
        # Build output, gitignored and regenerated on every build. Linting it is
        # not just redundant - it is unsatisfiable: the generated markdown links
        # to the site's own pages by absolute production URL, so every newly
        # added page fails the check until it is deployed, and a check that
        # cannot pass before the deploy that would fix it blocks the commit.
        # Excluded everywhere (not sandbox-gated): the chicken-and-egg is the
        # same in GitHub Actions. The source these files are generated from is
        # still linted.
        "-not",
        "-path",
        "*/dist/*",
        "-not",
        "-path",
        "*/.next/*",
        "-not",
        "-path",
        "*/.astro/*",
    ]
    if _in_claude_code_cloud():
        for path in CLOUD_SANDBOX_IGNORE_FILES:
            find_cmd.extend(["-not", "-path", path])

    md_files = (
        subprocess.run(
            find_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()
    )

    cmd.extend(md_files)
    return cmd


def _is_only_429(output: str) -> bool:
    """Check if all failures in the pytest output are 429s."""
    error_lines = re.findall(r"https?://\S+:\s+\d+:.*", output)
    if not error_lines:
        return False
    non_429 = [line for line in error_lines if "429" not in line]
    return len(non_429) == 0


def main() -> int:
    ignores = list(BOT_BLOCKED_DOMAINS)
    if _in_claude_code_cloud():
        print(
            "Claude Code cloud sandbox detected (CLAUDE_CODE_REMOTE=true): "
            "skipping egress-restricted, exfil-sensitive domains that are kept "
            "off the network allowlist. These are still checked in normal CI.",
            file=sys.stderr,
        )
        ignores.extend(CLOUD_SANDBOX_IGNORES)
    cmd = _build_cmd(ignores)

    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    for attempt in range(MAX_RETRIES):
        if result.returncode == 0:
            return 0

        if not _is_only_429(combined):
            print(combined, file=sys.stderr)
            return result.returncode

        if attempt < MAX_RETRIES - 1:
            delay = BASE_DELAY * (2**attempt)
            print(
                f"Rate-limited (429). Retrying in {delay}s "
                f"(attempt {attempt + 2}/{MAX_RETRIES})...",
                file=sys.stderr,
            )
            time.sleep(delay)
            result = subprocess.run(cmd, capture_output=True, text=True)
            combined = result.stdout + result.stderr

    print(combined, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
