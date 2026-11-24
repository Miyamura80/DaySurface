"""Tests for scripts/check_api_key_header_examples.py (`BEARER_API_KEY`)."""

from __future__ import annotations

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
    ],
)
def test_ignores_correct_examples(line: str) -> None:
    assert not c.BEARER_API_KEY.search(line)


def test_allowlist_needs_path_and_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    line = 'f"Bearer {XAI_KEY}"'
    monkeypatch.setattr(c, "ALLOWLIST", {("docs/grok.md", "XAI_KEY"): "xAI key"})
    assert c.find_offenders("docs/grok.md", line) == []
    assert c.find_offenders("docs/other.md", line) == [(1, line)]
