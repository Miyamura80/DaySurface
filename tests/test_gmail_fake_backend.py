"""Gating tests for the e2e-only fake Gmail backend.

The fake (``services/_gmail_fake_backend.py``) lets the MCP-App e2e harness render
the Gmail apps offline. These tests pin the two invariants that keep it safe:
it is off unless explicitly enabled, and it hard-refuses in production - plus a
happy-path check that, when enabled, ``gmail_get_thread`` returns a real parsed
``GmailThread`` built from the fixtures (no linked account / OAuth / network).
"""

from __future__ import annotations

import pytest

from common import global_config
from models.gmail import GmailCurateInboxInput, GmailGetThreadInput
from services import gmail_svc
from services.gmail_curate_svc import gmail_curate_inbox
from services.gmail_messages_svc import gmail_get_thread
from tests.test_template import TestTemplate


class TestGmailFakeBackend(TestTemplate):
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GMAIL_FAKE_BACKEND", raising=False)
        assert gmail_svc._maybe_fake_gmail_client() is None

    def test_flag_other_than_1_stays_disabled(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "true")
        assert gmail_svc._maybe_fake_gmail_client() is None

    def test_enabled_serves_fixture_thread(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "dev", raising=False)
        thread = gmail_get_thread(GmailGetThreadInput(thread_id="t-1001"))
        assert thread.thread_id == "t-1001"
        assert len(thread.messages) == 1
        msg = thread.messages[0]
        assert msg.subject == "Series A term sheet - final redlines"
        assert msg.from_ is not None and "Dana Whitfield" in msg.from_
        # Real service parsing ran end-to-end: the html body + attachment metadata
        # came through the raw-Gmail-API fixture, not a hand-built GmailThread.
        assert msg.body_html is not None and "liquidation preference" in msg.body_html
        assert [a.filename for a in msg.attachments] == ["termsheet-v7.pdf"]
        # Date is derived from internalDate (not the Date header); pin the year so
        # the fixture stays in sync with the Date header + dev-preview fixture.
        assert msg.date is not None and msg.date.year == 2026

    def test_enabled_serves_curated_inbox(self, monkeypatch):
        # The gmail_inbox app falls back to a curated-inbox refresh when it misses
        # the thread result, so the fake must render that path too (batch fetch).
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "dev", raising=False)
        result = gmail_curate_inbox(GmailCurateInboxInput())
        subjects = [t.subject for t in result.threads]
        assert "Series A term sheet - final redlines" in subjects
        thread = next(t for t in result.threads if t.thread_id == "t-1001")
        assert thread.from_ is not None and "Dana Whitfield" in thread.from_

    def test_unknown_thread_id_fails_loudly(self, monkeypatch):
        # An unknown/misspelled fixture id must raise (like Gmail's 404), not
        # synthesize an empty thread that could pass a render check with no content.
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "dev", raising=False)
        with pytest.raises(LookupError):
            gmail_get_thread(GmailGetThreadInput(thread_id="does-not-exist"))

    def test_refused_in_prod(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "prod", raising=False)
        with pytest.raises(RuntimeError, match="production"):
            gmail_svc._maybe_fake_gmail_client()
