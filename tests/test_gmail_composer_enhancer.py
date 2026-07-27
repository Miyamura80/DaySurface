"""Tests for the gmail_composer enhancer + app-only tool registration."""

import asyncio
from unittest.mock import MagicMock

from mcp_server.enhancers import get_enhancer
from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.gmail_composer import (
    INBOX_URI,
    gmail_compose_enhanced,
    gmail_update_draft_enhanced,
)
from mcp_server.server import mcp
from models.gmail import (
    GmailComposeInput,
    GmailDraft,
    GmailUpdateDraftInput,
)
from tests.test_template import TestTemplate


def _make_mock_ctx() -> MagicMock:
    """Minimal Context stand-in. The composer enhancers never call elicit, so we
    only stub what they touch: ``can_show_app`` is a pure env-var check."""
    return MagicMock()


def _fake_draft() -> GmailDraft:
    return GmailDraft(
        draft_id="d-1",
        to="bob@example.com",
        subject="Subj",
        body="Body",
    )


class TestGmailComposeEnhancer(TestTemplate):
    def test_attaches_app_when_can_show_app(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        draft = _fake_draft()

        def fake_service(_input: GmailComposeInput) -> GmailDraft:
            return draft

        tool: EnhancedTool[GmailComposeInput, GmailDraft] = EnhancedTool(
            ctx=_make_mock_ctx(),
            input=GmailComposeInput(
                user_id="alice", to="bob@example.com", subject="Subj", body="Body"
            ),
            service_fn=fake_service,
        )
        result = asyncio.run(gmail_compose_enhanced(tool))

        assert result is draft
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == INBOX_URI

    def test_skips_app_when_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")

        def fake_service(_input: GmailComposeInput) -> GmailDraft:
            return _fake_draft()

        tool: EnhancedTool[GmailComposeInput, GmailDraft] = EnhancedTool(
            ctx=_make_mock_ctx(),
            input=GmailComposeInput(
                user_id="alice", to="bob@example.com", subject="Subj", body="Body"
            ),
            service_fn=fake_service,
        )
        asyncio.run(gmail_compose_enhanced(tool))
        assert tool.app_meta() is None


class TestGmailUpdateDraftEnhancer(TestTemplate):
    def test_attaches_app_so_iframe_rerenders(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        draft = _fake_draft()

        def fake_service(_input: GmailUpdateDraftInput) -> GmailDraft:
            return draft

        tool: EnhancedTool[GmailUpdateDraftInput, GmailDraft] = EnhancedTool(
            ctx=_make_mock_ctx(),
            input=GmailUpdateDraftInput(user_id="alice", draft_id="d-1"),
            service_fn=fake_service,
        )
        result = asyncio.run(gmail_update_draft_enhanced(tool))

        assert result is draft
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == INBOX_URI

    def test_skips_app_when_disabled(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")

        def fake_service(_input: GmailUpdateDraftInput) -> GmailDraft:
            return _fake_draft()

        tool: EnhancedTool[GmailUpdateDraftInput, GmailDraft] = EnhancedTool(
            ctx=_make_mock_ctx(),
            input=GmailUpdateDraftInput(user_id="alice", draft_id="d-1"),
            service_fn=fake_service,
        )
        asyncio.run(gmail_update_draft_enhanced(tool))
        assert tool.app_meta() is None


class TestEnhancerRegistrationFallback(TestTemplate):
    """The fallback mode protects the headless path if the enhancer crashes."""

    def test_compose_registered_with_headless_fallback(self):
        entry = get_enhancer("gmail_compose")
        assert entry is not None
        assert entry.fallback == "headless"

    def test_update_draft_registered_with_headless_fallback(self):
        entry = get_enhancer("gmail_update_draft")
        assert entry is not None
        assert entry.fallback == "headless"


class TestGmailComposerAppTools(TestTemplate):
    """The four gmail_composer.* tools must be registered with app-only visibility."""

    def test_all_four_app_tools_registered(self):
        names = {
            "gmail_composer.save_draft",
            "gmail_composer.send",
            "gmail_composer.discard",
            "gmail_composer.refresh",
        }
        registered = set(mcp._tool_manager._tools)
        assert names.issubset(registered), names - registered

    def test_app_tools_have_visibility_app_meta(self):
        for tool_name in (
            "gmail_composer.save_draft",
            "gmail_composer.send",
            "gmail_composer.discard",
            "gmail_composer.refresh",
        ):
            tool = mcp._tool_manager._tools[tool_name]
            # FastMCP stores the meta dict on the Tool record under `meta`.
            meta = getattr(tool, "meta", None) or {}
            ui_meta = meta.get("ui") if isinstance(meta, dict) else None
            assert ui_meta and ui_meta.get("visibility") == ["app"], (
                f"{tool_name} missing app visibility meta: {meta!r}"
            )
