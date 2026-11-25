"""Tests for scripts/check_api_key_header_examples.py (`BEARER_API_KEY`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.check_api_key_header_examples as c


@pytest.mark.parametrize(
    "line",
    [
        'curl -H "Authorization: Bearer sk_live_..."',
        '-H "Authorization: Bearer $TOKEN" \\\\',
        '-H "Authorization: Bearer ${TOKEN}"',
        '-H "Authorization: Bearer $MYMCP_API_KEY"',
        '-H "Authorization: Bearer ${X_API_KEY}"',
        'headers={"Authorization": f"Bearer {KEY}"}',
        'headers={"Authorization": f"Bearer {api_key}"}',
        'headers={"Authorization": f"Bearer {self.api_key}"}',
        "Authorization: `Bearer ${process.env.API_KEY}`",
        '-H "Authorization: Bearer YOUR_API_KEY"',
        '-H "Authorization: Bearer <api-key>"',
        "Authorization: Bearer <your API key>",
    ],
)
def test_flags_api_key_in_bearer_slot(line: str) -> None:
    assert c.BEARER_API_KEY.search(line)


@pytest.mark.parametrize(
    "line",
    [
        'curl -H "X-API-KEY: sk_live_..."',
        "Call the MCP endpoint with `Authorization: Bearer <token>`",
        '`WWW-Authenticate: Bearer realm="mcp", resource_metadata="..."`',
        "| `401` | `unauthorized` | No | `Authorization: Bearer` (WorkOS JWT) |",
        "Authorization: Bearer <per-user JWT>",
        '-H "Authorization: Bearer $TOKENS_FILE"',
        "Authorization: Bearer <jwt>",
    ],
)
def test_ignores_correct_examples(line: str) -> None:
    assert not c.BEARER_API_KEY.search(line)


def test_allowlist_needs_path_and_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    line = 'f"Bearer {XAI_KEY}"'
    monkeypatch.setattr(c, "ALLOWLIST", {("docs/grok.md", "XAI_KEY"): "xAI key"})
    assert c.find_offenders("docs/grok.md", line) == []
    assert c.find_offenders("docs/other.md", line) == [(1, line)]


def test_tracked_files_drops_excluded_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = "docs/a.mdx\ntests/t.py\nservices/s.py\napi_server/x.py\nREADME.md\n"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=listed, stderr="")

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    kept = c.tracked_files()
    assert "docs/a.mdx" in kept and "README.md" in kept
    assert not any(p.startswith(c.EXCLUDED_DIRS) for p in kept)


def test_main_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text('-H "Authorization: Bearer sk_live_x"\n')
    (tmp_path / "good.md").write_text('-H "X-API-KEY: sk_live_x"\n')
    monkeypatch.setattr(c, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(c, "tracked_files", lambda: ["good.md", "bad.md"])
    assert c.main() == 1
    monkeypatch.setattr(c, "tracked_files", lambda: ["good.md"])
    assert c.main() == 0
