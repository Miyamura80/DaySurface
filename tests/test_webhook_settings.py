"""Tests for the Settings snapshot service + Settings MCP App wiring."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from mcp_server.app_tools import _auth_guard
from mcp_server.server import build_mcp_server
from models.webhook_settings import WebhookSettingsInput
from models.webhooks import WebhookSubscribeInput
from services.webhook_settings_svc import webhook_settings
from services.webhooks_svc import webhook_subscribe
from tests.test_template import TestTemplate


@contextmanager
def _patch_db():
    orig_engine = db_engine._engine
    orig_session = db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = factory
    try:
        yield factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


@contextmanager
def _plaintext_encryption():
    with patch(
        "services.webhooks_svc.require_encryption",
        return_value=PlaintextEncryption(),
    ):
        yield


class TestSettingsSnapshot(TestTemplate):
    def test_not_connected_no_subs(self):
        with (
            _patch_db(),
            patch.object(global_config, "GMAIL_PUBSUB_TOPIC", None),
        ):
            res = webhook_settings(WebhookSettingsInput(user_id="u1"))
            assert res.gmail_connected is False
            assert res.push_available is False
            assert res.subscriptions == []

    def test_connected_with_watch_and_subscription(self):
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch.object(global_config, "GMAIL_PUBSUB_TOPIC", "projects/p/topics/t"),
        ):
            with db_engine.use_db_session() as session:
                session.add(
                    GoogleToken(
                        user_id="u1",
                        email="me@example.com",
                        refresh_token_enc=b"x",
                        key_id="plaintext",
                        watch_history_id="500",
                    )
                )
                session.commit()
            webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url="https://hooks.example.com/h")
            )

            res = webhook_settings(WebhookSettingsInput(user_id="u1"))
            assert res.gmail_connected is True
            assert res.gmail_email == "me@example.com"
            assert res.watching is True
            assert res.push_available is True
            assert len(res.subscriptions) == 1
            # Snapshot must never leak the signing secret.
            assert not hasattr(res.subscriptions[0], "secret")


class TestSettingsAppWiring(TestTemplate):
    def test_tool_declares_app_and_apptools_registered(self):
        mcp = build_mcp_server()

        async def _collect():
            tools = await mcp.list_tools()
            resources = await mcp.list_resources()
            return tools, resources

        tools, resources = asyncio.run(_collect())
        by_name = {t.name: t for t in tools}

        # Trigger tool advertises the app so hosts can pre-fetch it.
        ws = by_name["webhook_settings"]
        meta = getattr(ws, "meta", None) or getattr(ws, "_meta", None) or {}
        assert meta.get("ui", {}).get("resourceUri") == "ui://mymcp/settings"

        # Guarded app-only tools the iframe calls.
        for name in (
            "settings.get",
            "settings.subscribe",
            "settings.rotate_secret",
            "settings.unsubscribe",
        ):
            assert name in by_name, f"missing app-tool {name}"

        # The app resource is registered.
        assert "ui://mymcp/settings" in {str(r.uri) for r in resources}


class TestAppToolUserGuard(TestTemplate):
    """guard_user_id must ignore a wire-supplied user_id when a principal is bound."""

    def test_bound_principal_overrides_wire_user_id(self):
        principal = SimpleNamespace(user_id="real-user")
        with (
            patch.object(_auth_guard, "current_user", return_value=principal),
            patch("mcp_server._tool_factory._check_scopes"),
            patch("mcp_server._tool_factory._check_quota"),
        ):
            # A tampered payload claiming another user's id is discarded.
            assert _auth_guard.guard_user_id("attacker-supplied") == "real-user"

    def test_no_principal_trusts_wire_user_id(self):
        with (
            patch.object(_auth_guard, "current_user", return_value=None),
            patch("mcp_server._tool_factory._check_scopes"),
            patch("mcp_server._tool_factory._check_quota"),
        ):
            # CLI / stdio: no auth context, so the supplied id is the only signal.
            assert _auth_guard.guard_user_id("cli-user") == "cli-user"
