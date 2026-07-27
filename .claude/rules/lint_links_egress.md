---
description: Decide allowlist vs CLOUD_SANDBOX_IGNORES when a link 403s under the Claude Code cloud egress proxy
globs:
  - "scripts/lint_links.py"
---

# lint_links: allowlist vs CLOUD_SANDBOX_IGNORES

`make lint_links` does live `GET`s on every external URL in the repo's markdown.
In the Claude Code web sandbox, egress goes through an **allowlist proxy that
`403`s any non-allowlisted host**, so a `403` usually means "not allowlisted,"
not a dead link. Confirm with `curl -s -o /dev/null -w "%{http_code}" <url>`; a
real break is usually `404`.

Pick a lever by the **exfil test**, not by how trustworthy the site is.
Allowlisting opens a host to *every* sandbox process (prompt-injected agents,
malicious deps), not just the linter.

## The exfil test

> Can an attacker push data to this host and **read it back**, or make it
> **relay a request onward** to a URL they choose?

Discount two non-channels: the host itself being compromised, and "blind"
logging where data lands in logs the attacker can't read (e.g. `GET /?secret=`).

- **No** (static docs/marketing, plain `GET` only): safe to grant standing
  egress. **Allowlist it** in the environment's **Network access -> Custom**
  field (keep "include default package managers" checked). Lives in environment
  config, **not** this script; makes the link pass in both sandbox and CI.
- **Yes** (programmable endpoint): **do not allowlist.** Add a pattern to
  `CLOUD_SANDBOX_IGNORES`, skipped in the sandbox only; CI (open egress) still
  checks it. Programmable = SSRF/relay (`shields.io` `?url=` fetch), RPC
  endpoints, package/tool registries (publish-then-read), render-and-share
  (carbon, chalk), live MCP/payment `POST`s, publish surfaces (VS Code
  Marketplace), or platform apexes carrying APIs/webhooks beyond the one link.
- **Many citation-only hosts in one file** (e.g. a research doc with ~20
  news/vendor links): don't allowlist all or add 20 patterns; add the file to
  `CLOUD_SANDBOX_IGNORE_FILES` (skipped in the sandbox `find`; CI still checks).

## Mechanics

- All sandbox-specific skips gate on `_in_claude_code_cloud()`
  (`CLAUDE_CODE_REMOTE=true`); never let one leak into local or GitHub Actions
  runs, which have open egress and must check every link.
- Patterns match with `re.match` (start-anchored, not end). End host patterns
  with `(/|\?|$)` so `shields.io` doesn't also match `shields.io.evil.com`.
- After allowlisting a host, confirm it's still linked outside any ignored file;
  don't allowlist hosts that only appeared via a `CLOUD_SANDBOX_IGNORE_FILES`
  entry.
