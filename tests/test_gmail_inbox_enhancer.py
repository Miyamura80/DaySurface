"""Tests for the gmail_inbox enhancer + app-only tool registrations."""

import asyncio
from unittest.mock import MagicMock

from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.gmail_inbox import (
    APP_URI,
    gmail_curate_inbox_enhanced,
    gmail_get_thread_enhanced,
    inbox_get_curation_enhanced,
)
from mcp_server.server import build_mcp_server
from models.curation import (
    CoverageSummary,
    CurationRecord,
    GetCurationInput,
    GetCurationResult,
)
from models.gmail import (
    GmailCuratedThread,
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailGetThreadInput,
    GmailThread,
    GmailThreadMessage,
)
from tests.test_template import TestTemplate


def _make_ctx() -> MagicMock:
    """Minimal Context stand-in. The inbox enhancer never calls elicit; it only
    checks ``can_show_app`` (pure env-var check)."""
    return MagicMock()


def _fake_service(_input: GmailCurateInboxInput) -> GmailCurateInboxResult:
    return GmailCurateInboxResult(
        threads=[
            GmailCuratedThread.model_validate(
                {
                    "thread_id": "tA",
                    "subject": "VIP",
                    "from": "ceo@example.com",
                    "snippet": "hi",
                    "importance_score": 0.9,
                    "reasons": ["Unread"],
                }
            )
        ]
    )


class TestGmailInboxEnhancer(TestTemplate):
    def test_attaches_app_when_capabilities_allow(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult] = (
            EnhancedTool(
                ctx=_make_ctx(),
                input=GmailCurateInboxInput(user_id="alice"),
                service_fn=_fake_service,
            )
        )
        result = asyncio.run(gmail_curate_inbox_enhanced(tool))
        assert isinstance(result, GmailCurateInboxResult)
        assert tool.app_resource_uri == APP_URI
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == "ui://mymcp/gmail_inbox"

    def test_disabled_via_env_skips_app(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult] = (
            EnhancedTool(
                ctx=_make_ctx(),
                input=GmailCurateInboxInput(user_id="alice"),
                service_fn=_fake_service,
            )
        )
        asyncio.run(gmail_curate_inbox_enhanced(tool))
        assert tool.app_resource_uri is None
        assert tool.app_meta() is None

    def test_returns_service_result_unchanged(self):
        tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult] = (
            EnhancedTool(
                ctx=_make_ctx(),
                input=GmailCurateInboxInput(user_id="alice"),
                service_fn=_fake_service,
            )
        )
        result = asyncio.run(gmail_curate_inbox_enhanced(tool))
        assert len(result.threads) == 1
        assert result.threads[0].thread_id == "tA"


def _fake_curation(_input: GetCurationInput) -> GetCurationResult:
    return GetCurationResult(
        records=[CurationRecord(thread_id="tA", summary="Investor deck due")],
        coverage=CoverageSummary(curated=1, stale=0, uncurated=2),
    )


class TestInboxGetCurationEnhancer(TestTemplate):
    def test_attaches_app_when_capabilities_allow(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        tool: EnhancedTool[GetCurationInput, GetCurationResult] = EnhancedTool(
            ctx=_make_ctx(),
            input=GetCurationInput(user_id="alice"),
            service_fn=_fake_curation,
        )
        result = asyncio.run(inbox_get_curation_enhanced(tool))
        assert isinstance(result, GetCurationResult)
        assert result.coverage.uncurated == 2
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == APP_URI

    def test_disabled_via_env_skips_app(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool: EnhancedTool[GetCurationInput, GetCurationResult] = EnhancedTool(
            ctx=_make_ctx(),
            input=GetCurationInput(user_id="alice"),
            service_fn=_fake_curation,
        )
        asyncio.run(inbox_get_curation_enhanced(tool))
        assert tool.app_meta() is None


def _fake_thread(_input: GmailGetThreadInput) -> GmailThread:
    return GmailThread(
        thread_id="t1",
        messages=[
            GmailThreadMessage.model_validate(
                {
                    "message_id": "m1",
                    "from": "alice@example.com",
                    "subject": "Hello",
                    "body_text": "Hi there",
                    "attachments": [],
                }
            )
        ],
    )


class TestGmailGetThreadEnhancer(TestTemplate):
    def test_attaches_app_when_capabilities_allow(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        tool: EnhancedTool[GmailGetThreadInput, GmailThread] = EnhancedTool(
            ctx=_make_ctx(),
            input=GmailGetThreadInput(user_id="alice", thread_id="t1"),
            service_fn=_fake_thread,
        )
        result = asyncio.run(gmail_get_thread_enhanced(tool))
        assert isinstance(result, GmailThread)
        assert result.thread_id == "t1"
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == APP_URI

    def test_disabled_via_env_skips_app(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool: EnhancedTool[GmailGetThreadInput, GmailThread] = EnhancedTool(
            ctx=_make_ctx(),
            input=GmailGetThreadInput(user_id="alice", thread_id="t1"),
            service_fn=_fake_thread,
        )
        asyncio.run(gmail_get_thread_enhanced(tool))
        assert tool.app_meta() is None


class TestGmailInboxAppTools(TestTemplate):
    """The five app-only tools are registered and tagged with visibility=['app']."""

    EXPECTED = [
        "gmail_inbox.refresh",
        "gmail_inbox.open_thread",
        "gmail_inbox.mark_read",
        "gmail_inbox.archive",
        "gmail_inbox.reply",
    ]

    def test_all_five_tools_registered(self):
        m = build_mcp_server()
        for name in self.EXPECTED:
            assert name in m._tool_manager._tools, f"missing {name}"

    def test_all_carry_app_visibility_meta(self):
        m = build_mcp_server()
        for name in self.EXPECTED:
            tool = m._tool_manager._tools[name]
            # FastMCP stores meta either under ``annotations``, ``meta`` or
            # the FastMCPTool's own ``__dict__``; the canonical location for
            # custom keys is ``_meta`` on the underlying MCP Tool. Reach in
            # via the public ``to_mcp_tool`` style or fallback to dict.
            meta = getattr(tool, "meta", None) or tool.__dict__.get("meta")
            assert meta is not None, f"{name} has no meta attached"
            ui = meta.get("ui") if isinstance(meta, dict) else None
            assert ui is not None and ui.get("visibility") == ["app"], (
                f"{name} missing visibility=['app']: {meta!r}"
            )
