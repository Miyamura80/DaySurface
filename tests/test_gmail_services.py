"""Tests for the Phase 4 headless Gmail services + helpers.

We mock ``_get_gmail_client`` to return a ``MagicMock`` whose chained
``.users().drafts()...`` calls produce canned Gmail API payloads. This
avoids touching ``googleapiclient`` itself (the chained-resource style is
notoriously hard to mock) while still exercising the real service code,
the MIME helpers, and the Pydantic mapping.
"""

from __future__ import annotations

import base64
import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from mcp_server.app_tools.gmail_composer import (
    _coerce_attachments,
    _patch_attachments,
)
from mcp_server.app_tools.gmail_composer import save_draft as _save_draft
from models.gmail import (
    UNSET,
    AttachmentInput,
    AttachmentReference,
    GmailAddAttachmentInput,
    GmailComposeInput,
    GmailCurateInboxInput,
    GmailDiscardDraftInput,
    GmailGetAttachmentInput,
    GmailGetDraftInput,
    GmailGetThreadInput,
    GmailListDraftsInput,
    GmailListInboxInput,
    GmailRemoveAttachmentInput,
    GmailSendInput,
    GmailThread,
    GmailUpdateDraftInput,
)
from services import get_registry, gmail_svc
from services.gmail_attachments_svc import (
    gmail_add_attachment,
    gmail_remove_attachment,
)
from services.gmail_curate_svc import gmail_curate_inbox
from services.gmail_draft_helpers import draft_thread_map, find_draft_id_for_thread
from services.gmail_drafts_svc import (
    GmailReplyInput,
    gmail_compose,
    gmail_discard_draft,
    gmail_get_draft,
    gmail_list_drafts,
    gmail_reply_to_thread,
    gmail_send,
    gmail_update_draft,
)
from services.gmail_messages_svc import (
    GmailThreadModifyInput,
    gmail_archive_thread,
    gmail_get_attachment,
    gmail_get_thread,
    gmail_list_inbox,
    gmail_mark_thread_read,
)
from services.gmail_svc import (
    GmailAttachmentTooLargeError,
    GmailNotConnectedError,
    _build_raw_message,
    _get_gmail_client,
    _parse_message_resource,
)
from tests.test_template import TestTemplate

# ---------------------------------------------------------------------------
# DB fixture (same pattern as tests/test_google_oauth.py)
# ---------------------------------------------------------------------------


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
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    try:
        yield session_factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


@pytest.fixture(autouse=True)
def _clean_client_cache():
    """``gmail_svc._client_cache`` is module-global state shared by every test
    in this file, and ``_get_gmail_client``'s per-call token-row check means a
    leaked entry changes what later tests observe - so isolate the whole file,
    not just the cache-focused class."""
    gmail_svc._client_cache.clear()
    yield
    gmail_svc._client_cache.clear()


def _seed_token(factory, user_id: str = "alice") -> None:
    s = factory()
    s.add(
        GoogleToken(
            user_id=user_id,
            email=f"{user_id}@example.com",
            refresh_token_enc=b"RT",
            key_id="plaintext",
            scopes=["openid", "email"],
        )
    )
    s.commit()
    s.close()


# ---------------------------------------------------------------------------
# Helpers for building fake Gmail API payloads
# ---------------------------------------------------------------------------


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def _headers(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in d.items()]


def _plain_message(
    *,
    message_id: str = "m-1",
    thread_id: str = "t-1",
    headers: dict[str, str] | None = None,
    body: str = "hello world",
    snippet: str = "hello world",
    internal_date_ms: int | None = None,
    label_ids: list[str] | None = None,
) -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "internalDate": str(internal_date_ms) if internal_date_ms else "1700000000000",
        "labelIds": label_ids or [],
        "payload": {
            "mimeType": "text/plain",
            "headers": _headers(
                headers or {"From": "a@x", "To": "b@y", "Subject": "hi"}
            ),
            "body": {"data": _b64url(body), "size": len(body)},
        },
    }


def _draft_resource(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    body: str = "hello world",
    thread_id: str = "t-1",
) -> dict:
    return {
        "id": draft_id,
        "message": _plain_message(
            message_id=f"m-{draft_id}",
            thread_id=thread_id,
            headers={"To": to, "Subject": subject},
            body=body,
            snippet=body[:50],
        ),
    }


def _draft_resource_with_attachment(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    body: str = "hello world",
    thread_id: str = "t-1",
    attachment_id: str = "att-1",
    filename: str = "report.pdf",
    mime_type: str = "application/pdf",
    size: int = 1024,
    cc: str | None = None,
) -> dict:
    """A draft whose message is multipart/mixed with one named attachment."""
    headers = {"To": to, "Subject": subject}
    if cc is not None:
        headers["Cc"] = cc
    return {
        "id": draft_id,
        "message": {
            "id": f"m-{draft_id}",
            "threadId": thread_id,
            "snippet": body[:50],
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": _headers(headers),
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url(body), "size": len(body)},
                    },
                    {
                        "mimeType": mime_type,
                        "filename": filename,
                        "body": {"attachmentId": attachment_id, "size": size},
                    },
                ],
            },
        },
    }


def _gmail_attachment_blob(raw: bytes = b"PDFBYTES") -> dict:
    """Mimic ``messages().attachments().get()`` - base64url data, padding stripped."""
    data = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return {"data": data, "size": len(raw)}


def _attachment_filenames_in_raw(raw_b64: str) -> list[str]:
    """Filenames of attachment parts inside a base64url-encoded MIME message."""
    mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
    return [name for p in mime.walk() if (name := p.get_filename())]


def _last_update_raw(mock: MagicMock) -> str:
    """The raw MIME of the most recent ``drafts().update(body=...)`` call."""
    update_calls = [c for c in mock.users().drafts().update.call_args_list if c.kwargs]
    assert update_calls, "drafts().update() was not called with kwargs"
    return update_calls[-1].kwargs["body"]["message"]["raw"]


def _make_mock_service() -> MagicMock:
    """A MagicMock that supports the chained ``.users().drafts().get().execute()`` style."""
    mock = MagicMock()
    mock.users().labels().list().execute.return_value = {"labels": []}
    mock.users().drafts().list().execute.return_value = {"drafts": []}
    return mock


# ---------------------------------------------------------------------------
# MIME helper tests (no Gmail mock needed)
# ---------------------------------------------------------------------------


class TestBuildRawMessage(TestTemplate):
    def test_round_trip(self):
        raw = _build_raw_message(
            to="b@example.com",
            subject="Subject!",
            body="Body text\nwith newline",
            cc="cc@example.com",
        )
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        msg = message_from_bytes(decoded)
        assert msg["To"] == "b@example.com"
        assert msg["Cc"] == "cc@example.com"
        assert msg["Subject"] == "Subject!"
        # get_payload(decode=True) returns the decoded body bytes for a
        # non-multipart message; cast for the type-checker.
        raw_payload = msg.get_payload(decode=True)
        assert isinstance(raw_payload, bytes)
        payload = raw_payload.decode("utf-8")
        assert "Body text" in payload
        assert "with newline" in payload


class TestParseMessageResource(TestTemplate):
    def test_plaintext_only(self):
        msg = _plain_message(body="plain body", headers={"From": "a@x", "Subject": "s"})
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] == "plain body"
        assert parsed["body_html"] is None
        assert parsed["from"] == "a@x"
        assert parsed["subject"] == "s"
        assert parsed["attachments"] == []
        assert isinstance(parsed["date"], datetime)

    def test_html_only(self):
        msg = {
            "id": "m",
            "threadId": "t",
            "snippet": "snip",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/html",
                "headers": _headers({"Subject": "html"}),
                "body": {"data": _b64url("<p>hi</p>"), "size": 9},
            },
        }
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] is None
        assert parsed["body_html"] == "<p>hi</p>"

    def test_multipart_with_attachment(self):
        msg = {
            "id": "m",
            "threadId": "t",
            "snippet": "snip",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": _headers({"From": "a@x"}),
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("text part"), "size": 9},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64url("<p>html part</p>"), "size": 16},
                    },
                    {
                        "mimeType": "application/pdf",
                        "filename": "report.pdf",
                        "body": {"attachmentId": "att-1", "size": 1024},
                    },
                ],
            },
        }
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] == "text part"
        assert parsed["body_html"] == "<p>html part</p>"
        assert len(parsed["attachments"]) == 1
        att = parsed["attachments"][0]
        assert att["filename"] == "report.pdf"
        assert att["mime_type"] == "application/pdf"
        assert att["size"] == 1024
        assert att["attachment_id"] == "att-1"


# ---------------------------------------------------------------------------
# Service tests (mock _get_gmail_client)
# ---------------------------------------------------------------------------


def _patch_client(mock_svc: MagicMock):
    # Patch every import site so each service module picks it up.
    return [
        patch("services.gmail_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_drafts_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_messages_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_curate_svc._get_gmail_client", return_value=mock_svc),
        patch(
            "services.gmail_attachments_svc._get_gmail_client", return_value=mock_svc
        ),
    ]


def _apply(patches):
    return [p.start() for p in patches]


def _stop(patches):
    for p in patches:
        p.stop()


class TestGmailListDrafts(TestTemplate):
    def test_happy_path(self):
        draft_payloads = [
            {
                "id": "d-1",
                "message": {
                    "id": "m-d-1",
                    "snippet": "Hi Alice",
                    "internalDate": "1700000000000",
                    "payload": {
                        "headers": _headers(
                            {"To": "alice@example.com", "Subject": "Hello"}
                        ),
                    },
                },
            },
            {
                "id": "d-2",
                "message": {
                    "id": "m-d-2",
                    "snippet": "Hi Bob",
                    "internalDate": "1700000001000",
                    "payload": {
                        "headers": _headers(
                            {"To": "bob@example.com", "Subject": "Hey"}
                        ),
                    },
                },
            },
        ]

        class FakeBatch:
            def __init__(self):
                self._queue: list[tuple] = []

            def add(self, req, callback):
                self._queue.append((req, callback))

            def execute(self):
                for i, (_req, cb) in enumerate(self._queue):
                    cb(str(i), draft_payloads[i], None)

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().list().execute.return_value = {
                "drafts": [{"id": "d-1"}, {"id": "d-2"}],
            }
            mock.new_batch_http_request.return_value = FakeBatch()

            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_list_drafts(
                    GmailListDraftsInput(user_id="alice", limit=10)
                )
            finally:
                _stop(patches)

        assert len(result.drafts) == 2
        assert result.drafts[0].draft_id == "d-1"
        assert result.drafts[0].to == "alice@example.com"
        assert result.drafts[0].subject == "Hello"


class TestGmailGetDraft(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-1",
                to="alice@example.com",
                subject="Hello",
                body="The body",
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_get_draft(
                    GmailGetDraftInput(user_id="alice", draft_id="d-1")
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-1"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Hello"
        assert draft.body == "The body"


class TestGmailUpdateDraft(TestTemplate):
    def test_only_body_patched_preserves_to_and_subject(self):
        original = _draft_resource(
            draft_id="d-1",
            to="alice@example.com",
            subject="Original Subject",
            body="Original body",
        )
        updated_resource = _draft_resource(
            draft_id="d-1",
            to="alice@example.com",
            subject="Original Subject",
            body="New body",
        )

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            # get() is called twice: the pre-read (current state) and the
            # post-update re-fetch (saved state the response echoes).
            mock.users().drafts().get().execute.side_effect = [
                original,
                updated_resource,
            ]
            mock.users().drafts().update().execute.return_value = updated_resource

            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", body="New body"
                    )
                )
            finally:
                _stop(patches)

        # Verify the update call carried over the preserved fields by decoding
        # the raw MIME passed to drafts().update(body=...).
        update_calls = [
            c for c in mock.users().drafts().update.call_args_list if c.kwargs
        ]
        assert update_calls, "drafts().update() was not called with kwargs"
        last = update_calls[-1]
        raw_b64 = last.kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "alice@example.com"
        assert mime["Subject"] == "Original Subject"
        decoded_payload = mime.get_payload(decode=True)
        assert isinstance(decoded_payload, bytes)
        assert "New body" in decoded_payload.decode("utf-8")

        assert draft.draft_id == "d-1"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Original Subject"
        assert draft.body == "New body"


class TestGmailCompose(TestTemplate):
    def test_returns_populated_draft(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().create().execute.return_value = {"id": "d-new"}
            # compose re-fetches the saved state at format=full after create.
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-new",
                to="alice@example.com",
                subject="Subj",
                body="Body!",
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_compose(
                    GmailComposeInput(
                        user_id="alice",
                        to="alice@example.com",
                        subject="Subj",
                        body="Body!",
                    )
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-new"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Subj"
        assert draft.body == "Body!"


class TestGmailSend(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().send().execute.return_value = {
                "id": "msg-123",
                "threadId": "thr-7",
                "labelIds": ["SENT"],
            }
            patches = _patch_client(mock)
            _apply(patches)
            try:
                before = datetime.now(UTC)
                result = gmail_send(GmailSendInput(user_id="alice", draft_id="d-1"))
                after = datetime.now(UTC)
            finally:
                _stop(patches)

        assert result.message_id == "msg-123"
        assert result.thread_id == "thr-7"
        assert before <= result.sent_at <= after


class TestGmailDiscardDraft(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().delete().execute.return_value = {}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_discard_draft(
                    GmailDiscardDraftInput(user_id="alice", draft_id="d-1")
                )
            finally:
                _stop(patches)

        assert result.discarded is True


# ---------------------------------------------------------------------------
# Inbox / threads / curate
# ---------------------------------------------------------------------------


class TestGmailListInbox(TestTemplate):
    def test_happy_path_uses_from_alias(self):
        msg_payloads = {
            "m-1": {
                "id": "m-1",
                "threadId": "t-1",
                "snippet": "snip 1",
                "internalDate": "1700000000000",
                "payload": {
                    "headers": _headers(
                        {
                            "From": "sender1@example.com",
                            "Subject": "S1",
                            "Date": "Wed, 15 Nov 2023 00:00:00 +0000",
                        }
                    )
                },
            },
            "m-2": {
                "id": "m-2",
                "threadId": "t-2",
                "snippet": "snip 2",
                "internalDate": "1700000001000",
                "payload": {
                    "headers": _headers(
                        {"From": "sender2@example.com", "Subject": "S2"}
                    )
                },
            },
        }

        def fake_batch_get_messages(svc, ids, **kwargs):
            return {mid: msg_payloads[mid] for mid in ids if mid in msg_payloads}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().list().execute.return_value = {
                "messages": [{"id": "m-1"}, {"id": "m-2"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_messages_svc._batch_get_messages",
                side_effect=fake_batch_get_messages,
            ):
                try:
                    result = gmail_list_inbox(
                        GmailListInboxInput(user_id="alice", limit=5)
                    )
                finally:
                    _stop(patches)

        assert len(result.messages) == 2
        assert result.messages[0].from_ == "sender1@example.com"
        assert result.messages[0].subject == "S1"
        dumped = result.messages[0].model_dump(by_alias=True)
        assert dumped["from"] == "sender1@example.com"


class TestGmailGetThread(TestTemplate):
    def test_thread_with_two_messages_and_attachment(self):
        # Build a thread payload directly
        thread_payload = {
            "id": "t-9",
            "messages": [
                _plain_message(
                    message_id="m-a",
                    thread_id="t-9",
                    headers={
                        "From": "a@x.com",
                        "To": "b@y.com",
                        "Subject": "Re: stuff",
                    },
                    body="first",
                ),
                {
                    "id": "m-b",
                    "threadId": "t-9",
                    "snippet": "second",
                    "internalDate": "1700000005000",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": _headers(
                            {
                                "From": "b@y.com",
                                "To": "a@x.com",
                                "Subject": "Re: stuff",
                            }
                        ),
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64url("reply body"), "size": 10},
                            },
                            {
                                "mimeType": "application/pdf",
                                "filename": "doc.pdf",
                                "body": {"attachmentId": "att-9", "size": 42},
                            },
                        ],
                    },
                },
            ],
        }

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = thread_payload
            patches = _patch_client(mock)
            _apply(patches)
            try:
                thread = gmail_get_thread(
                    GmailGetThreadInput(user_id="alice", thread_id="t-9")
                )
            finally:
                _stop(patches)

        assert thread.thread_id == "t-9"
        assert len(thread.messages) == 2
        assert thread.messages[0].body_text == "first"
        assert thread.messages[0].from_ == "a@x.com"
        assert thread.messages[1].body_text == "reply body"
        assert len(thread.messages[1].attachments) == 1
        assert thread.messages[1].attachments[0].filename == "doc.pdf"
        assert thread.messages[1].attachments[0].attachment_id == "att-9"

    def _inline_image_thread(self) -> dict:
        """A one-message thread whose HTML body references a cid: inline image."""
        return {
            "id": "t-img",
            "messages": [
                {
                    "id": "m-img",
                    "threadId": "t-img",
                    "snippet": "hi",
                    "internalDate": "1700000000000",
                    "payload": {
                        "mimeType": "multipart/related",
                        "headers": _headers(
                            {"From": "a@x", "To": "b@y", "Subject": "s"}
                        ),
                        "parts": [
                            {
                                "mimeType": "text/html",
                                "body": {
                                    "data": _b64url('<p>sig <img src="cid:logo1"></p>'),
                                    "size": 30,
                                },
                            },
                            {
                                "mimeType": "image/png",
                                "filename": "",
                                "headers": _headers({"Content-ID": "<logo1>"}),
                                "body": {
                                    "attachmentId": "att-img",
                                    "size": 8,
                                    "data": base64.urlsafe_b64encode(b"PNGDATA1")
                                    .decode("ascii")
                                    .rstrip("="),
                                },
                            },
                        ],
                    },
                }
            ],
        }

    def _run_get_thread(
        self, thread_payload: dict, **kwargs
    ) -> tuple[GmailThread, MagicMock]:
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = thread_payload
            patches = _patch_client(mock)
            _apply(patches)
            try:
                thread = gmail_get_thread(
                    GmailGetThreadInput(user_id="alice", thread_id="t-x", **kwargs)
                )
                return thread, mock
            finally:
                _stop(patches)

    def test_attachment_data_omitted_by_default(self):
        thread, mock = self._run_get_thread(self._inline_image_thread())
        att = thread.messages[0].attachments[0]
        # Locators are preserved so the caller can fetch bytes on demand...
        assert att.attachment_id == "att-img"
        assert att.content_id == "logo1"
        assert att.mime_type == "image/png"
        # ...but the base64 blob and the inlined data: URI are gone.
        assert att.data is None
        assert "cid:logo1" in (thread.messages[0].body_html or "")
        assert "data:image" not in (thread.messages[0].body_html or "")
        # No per-image attachments.get() fetch happened for inlining.
        mock.users().messages().attachments().get().execute.assert_not_called()

    def test_include_attachment_data_inlines_images(self):
        thread, _mock = self._run_get_thread(
            self._inline_image_thread(), include_attachment_data=True
        )
        att = thread.messages[0].attachments[0]
        assert att.data is not None
        # cid: reference is rewritten to a data: URI in the HTML body.
        body_html = thread.messages[0].body_html or ""
        assert "data:image/png;base64," in body_html
        assert "cid:logo1" not in body_html

    def test_strip_quoted_replies_drops_history(self):
        body = "My new reply\nOn Mon, someone <a@x.com> wrote:\n> old line\n> more"
        thread_payload = {
            "id": "t-q",
            "messages": [
                _plain_message(
                    message_id="m-q",
                    thread_id="t-q",
                    headers={"From": "a@x", "Subject": "Re: s"},
                    body=body,
                    snippet="My new reply",
                )
            ],
        }
        thread, _mock = self._run_get_thread(thread_payload, strip_quoted_replies=True)
        assert thread.messages[0].body_text == "My new reply"

    def test_quoted_replies_kept_by_default(self):
        body = "My new reply\nOn Mon, someone <a@x.com> wrote:\n> old line\n> more"
        thread_payload = {
            "id": "t-q",
            "messages": [
                _plain_message(
                    message_id="m-q",
                    thread_id="t-q",
                    headers={"From": "a@x", "Subject": "Re: s"},
                    body=body,
                    snippet="My new reply",
                )
            ],
        }
        thread, _mock = self._run_get_thread(thread_payload)
        assert "old line" in (thread.messages[0].body_text or "")

    def test_draft_on_second_drafts_page_is_found(self):
        """Regression: with >50 drafts, a draft past page 1 must still be found.

        The old inline scan called ``drafts().list(maxResults=50)`` with no
        ``nextPageToken`` follow-up, so a draft attached to the thread but
        listed on a later page was silently reported as "no draft".
        """
        thread_payload = {
            "id": "t-deep",
            "messages": [
                _plain_message(
                    message_id="m-1",
                    thread_id="t-deep",
                    headers={"From": "a@x", "To": "b@y", "Subject": "hi"},
                    body="original",
                ),
                # threads().get(format=full) includes the draft's underlying
                # message; its DRAFT label is what gates the drafts().list scan.
                _plain_message(
                    message_id="m-d-deep",
                    thread_id="t-deep",
                    headers={"To": "b@y", "Subject": "hi"},
                    body="draft reply",
                    label_ids=["DRAFT"],
                ),
            ],
        }
        page1 = {
            "drafts": [
                {
                    "id": f"d-{i}",
                    "message": {"id": f"m-d-{i}", "threadId": f"t-other-{i}"},
                }
                for i in range(50)
            ],
            "nextPageToken": "page-2",
        }
        page2 = {
            "drafts": [
                {"id": "d-deep", "message": {"id": "m-d-deep", "threadId": "t-deep"}}
            ]
        }
        target_draft = _draft_resource(
            draft_id="d-deep", thread_id="t-deep", body="draft reply"
        )

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = thread_payload
            mock.users().drafts().list().execute.side_effect = [page1, page2]
            mock.users().drafts().get().execute.return_value = target_draft
            patches = _patch_client(mock)
            _apply(patches)
            try:
                thread = gmail_get_thread(
                    GmailGetThreadInput(user_id="alice", thread_id="t-deep")
                )
            finally:
                _stop(patches)

        assert thread.draft is not None
        assert thread.draft.draft_id == "d-deep"
        assert thread.draft.body == "draft reply"
        # The draft's underlying message is not doubled into the thread.
        assert [m.message_id for m in thread.messages] == ["m-1"]
        # The scan actually followed nextPageToken onto page 2.
        list_kwargs = [
            c.kwargs for c in mock.users().drafts().list.call_args_list if c.kwargs
        ]
        assert any(k.get("pageToken") == "page-2" for k in list_kwargs)

    def test_no_draft_thread_skips_drafts_scan(self):
        """A thread with no DRAFT-labeled message costs zero drafts API calls."""
        thread_payload = {
            "id": "t-x",
            "messages": [
                _plain_message(message_id="m-1", thread_id="t-x", body="hello")
            ],
        }
        thread, mock = self._run_get_thread(thread_payload)
        assert thread.draft is None
        mock.users().drafts().list().execute.assert_not_called()


class TestDraftThreadMapHelpers(TestTemplate):
    """Direct tests for the shared paginated thread→draft scan."""

    def _two_page_mock(self) -> MagicMock:
        """Two drafts on the same thread, split across two drafts.list pages."""
        page1 = {
            "drafts": [
                {"id": "d-first", "message": {"id": "m-1", "threadId": "t-dup"}},
                {"id": "d-other", "message": {"id": "m-2", "threadId": "t-other"}},
            ],
            "nextPageToken": "page-2",
        }
        page2 = {
            "drafts": [
                {"id": "d-second", "message": {"id": "m-3", "threadId": "t-dup"}}
            ]
        }
        mock = MagicMock()
        mock.users().drafts().list().execute.side_effect = [page1, page2]
        return mock

    def test_find_draft_id_first_match_wins_and_early_exits(self):
        mock = self._two_page_mock()
        assert find_draft_id_for_thread(mock, "t-dup") == "d-first"
        # Early exit: page 2 was never fetched.
        assert mock.users().drafts().list().execute.call_count == 1

    def test_draft_thread_map_first_match_wins(self):
        mock = self._two_page_mock()
        mapping = draft_thread_map(mock)
        assert mapping["t-dup"] == "d-first"
        assert mapping["t-other"] == "d-other"
        # The full map consumed both pages.
        assert mock.users().drafts().list().execute.call_count == 2


class TestGmailGetAttachment(TestTemplate):
    def test_returns_normalized_base64(self):
        raw = b"\xff\xfe\xfd\xfc\xfb"  # bytes whose b64 uses - and _ chars
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob(raw)
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_get_attachment(
                    GmailGetAttachmentInput(
                        user_id="alice", message_id="m-1", attachment_id="att-1"
                    )
                )
            finally:
                _stop(patches)

        assert result.message_id == "m-1"
        assert result.attachment_id == "att-1"
        # Standard, padded base64 that decodes back to the original bytes.
        assert base64.b64decode(result.data_base64) == raw

    def test_rejects_attachment_over_size_cap(self):
        blob = _gmail_attachment_blob(b"PDFBYTES")
        # A numeric *string* size (Gmail can return numbers as strings) must
        # still be coerced and caught by the cap, not silently bypass it.
        blob["size"] = "10000"  # bytes, over the patched cap below
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().attachments().get().execute.return_value = blob
            patches = _patch_client(mock)
            _apply(patches)
            try:
                with (
                    patch.object(global_config.gmail, "max_attachment_bytes", 5),
                    pytest.raises(
                        GmailAttachmentTooLargeError, match="over the 5-byte limit"
                    ),
                ):
                    gmail_get_attachment(
                        GmailGetAttachmentInput(
                            user_id="alice", message_id="m-1", attachment_id="att-1"
                        )
                    )
            finally:
                _stop(patches)

    def test_missing_size_estimated_from_payload_and_capped(self):
        # No 'size' metadata: the guard must estimate from the base64 payload
        # so a missing size can't bypass the cap.
        blob = _gmail_attachment_blob(b"PDFBYTESPDFBYTES")
        blob.pop("size", None)
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().attachments().get().execute.return_value = blob
            patches = _patch_client(mock)
            _apply(patches)
            try:
                with (
                    patch.object(global_config.gmail, "max_attachment_bytes", 2),
                    pytest.raises(GmailAttachmentTooLargeError),
                ):
                    gmail_get_attachment(
                        GmailGetAttachmentInput(
                            user_id="alice", message_id="m-1", attachment_id="att-1"
                        )
                    )
            finally:
                _stop(patches)


class TestGmailCurateInbox(TestTemplate):
    def test_ranks_by_score_deterministically(self):
        now = datetime.now(UTC)
        thread_a = {
            "id": "tA",
            "messages": [
                {
                    "id": "mA",
                    "labelIds": ["IMPORTANT", "UNREAD"],
                    "snippet": "vip!",
                    "internalDate": str(
                        int((now - timedelta(hours=1)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "ceo@example.com", "Subject": "VIP"}
                        )
                    },
                },
            ],
        }
        thread_b = {
            "id": "tB",
            "messages": [
                {
                    "id": "mB",
                    "labelIds": ["UNREAD"],
                    "snippet": "meh",
                    "internalDate": str(
                        int((now - timedelta(days=3)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "friend@example.com", "Subject": "hi"}
                        )
                    },
                },
            ],
        }
        thread_c = {
            "id": "tC",
            "messages": [
                {
                    "id": "mC",
                    "labelIds": [],
                    "snippet": "ancient",
                    "internalDate": str(
                        int((now - timedelta(days=14)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "old@example.com", "Subject": "old"}
                        )
                    },
                },
            ],
        }
        thread_map = {"tA": thread_a, "tB": thread_b, "tC": thread_c}

        def fake_batch_get_threads(svc, ids, **kwargs):
            return {tid: thread_map[tid] for tid in ids if tid in thread_map}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "tA"}, {"id": "tB"}, {"id": "tC"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_curate_svc._batch_get_threads",
                side_effect=fake_batch_get_threads,
            ):
                try:
                    result = gmail_curate_inbox(
                        GmailCurateInboxInput(user_id="alice", limit=10)
                    )
                finally:
                    _stop(patches)

        ids = [t.thread_id for t in result.threads]
        assert ids == ["tA", "tB", "tC"]
        assert result.threads[0].importance_score > result.threads[1].importance_score
        assert result.threads[1].importance_score > result.threads[2].importance_score
        dumped = result.threads[0].model_dump(by_alias=True)
        assert dumped["from"] == "ceo@example.com"

    def test_skips_thread_when_batch_fetch_omits_it(self):
        """Threads missing from the batch result (e.g. deleted) are silently skipped."""
        good_thread = {
            "id": "good",
            "messages": [
                {
                    "id": "mG",
                    "labelIds": ["IMPORTANT"],
                    "snippet": "ok",
                    "internalDate": "1700000000000",
                    "payload": {"headers": _headers({"Subject": "ok"})},
                },
            ],
        }

        def fake_batch_get_threads(svc, ids, **kwargs):
            return {"good": good_thread}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "good"}, {"id": "bad"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_curate_svc._batch_get_threads",
                side_effect=fake_batch_get_threads,
            ):
                try:
                    result = gmail_curate_inbox(
                        GmailCurateInboxInput(user_id="alice", limit=10)
                    )
                finally:
                    _stop(patches)

        assert [t.thread_id for t in result.threads] == ["good"]


class TestGmailNotConnected(TestTemplate):
    def test_raises_when_no_row(self):
        with _patch_db(), pytest.raises(GmailNotConnectedError) as excinfo:
            _get_gmail_client("nonexistent-user")
        assert excinfo.value.user_id == "nonexistent-user"

    def test_message_is_self_recovering(self):
        """The error text is what the MCP host LLM sees (isError tool result),
        so it must contain the full recovery path, not just the diagnosis."""
        err = GmailNotConnectedError("u-123")
        msg = str(err)
        assert "u-123" in msg
        assert "gmail_connect" in msg
        assert "auth_url" in msg
        assert "retry" in msg
        assert "gmail_status" in msg


class TestGmailClientCache(TestTemplate):
    """Bounded/evicting client cache + cross-process disconnect fail-closed.

    Cache isolation comes from the module-level ``_clean_client_cache``
    autouse fixture above.
    """

    @staticmethod
    def _revoke_row(factory, user_id: str = "alice") -> None:
        """Revoke the token row directly in the DB, as ``gmail_disconnect``
        running in *another* process would (its ``_invalidate_gmail_client``
        call never reaches this process's cache)."""
        s = factory()
        row = s.query(GoogleToken).filter_by(user_id=user_id).one()
        row.revoked_at = datetime.now(UTC)
        s.commit()
        s.close()

    def _build_patches(self, built_client: MagicMock):
        return (
            patch(
                "common.token_encryption.require_encryption",
                return_value=PlaintextEncryption(),
            ),
            patch("services.gmail_svc._mint_access_token", return_value="at-1"),
            patch("googleapiclient.discovery.build", return_value=built_client),
        )

    def test_expired_entries_evicted_on_any_lookup(self):
        """Expired tuples for *other* users are purged, not left until those
        users hit again - the cache no longer grows with the user population."""
        for i in range(5):
            gmail_svc._client_cache[f"departed-{i}"] = (time.time() - 1, MagicMock())
        live = MagicMock()
        gmail_svc._client_cache["alice"] = (time.time() + 60, live)

        with _patch_db() as factory:
            _seed_token(factory)
            assert _get_gmail_client("alice") is live

        assert set(gmail_svc._client_cache) == {"alice"}

    def test_cache_is_bounded_and_evicts_min_expiry_victim(self):
        # Distinct expiries so the eviction victim is deterministic: u-0 is
        # closest to expiry and must be the one displaced.
        now = time.time()
        for i in range(gmail_svc._CLIENT_CACHE_MAX):
            gmail_svc._client_cache[f"u-{i}"] = (now + 3600 + i, MagicMock())

        built = MagicMock()
        with _patch_db() as factory:
            _seed_token(factory)
            p1, p2, p3 = self._build_patches(built)
            with p1, p2, p3:
                assert _get_gmail_client("alice") is built

        assert len(gmail_svc._client_cache) == gmail_svc._CLIENT_CACHE_MAX
        assert "u-0" not in gmail_svc._client_cache  # min-expiry entry evicted
        assert "u-1" in gmail_svc._client_cache  # everything else survives
        assert gmail_svc._client_cache["alice"][1] is built

    def test_disconnect_in_other_process_fails_closed_on_cache_hit(self):
        """Seed a cached client (process A), revoke the row in the DB (process
        B's disconnect), then the next call in process A must raise
        ``GmailNotConnectedError`` instead of serving the stale authorized
        client for up to the 50-minute TTL."""
        with _patch_db() as factory:
            _seed_token(factory)
            stale = MagicMock()
            gmail_svc._client_cache["alice"] = (
                time.time() + gmail_svc._CLIENT_TTL_S,
                stale,
            )
            self._revoke_row(factory)

            with pytest.raises(GmailNotConnectedError) as excinfo:
                _get_gmail_client("alice")

        assert excinfo.value.user_id == "alice"
        assert "alice" not in gmail_svc._client_cache

    def test_active_row_still_serves_cache_hit(self):
        """The per-call token-row check must not break the happy path."""
        with _patch_db() as factory:
            _seed_token(factory)
            live = MagicMock()
            gmail_svc._client_cache["alice"] = (
                time.time() + gmail_svc._CLIENT_TTL_S,
                live,
            )
            assert _get_gmail_client("alice") is live
            assert _get_gmail_client("alice") is live


class TestDraftRoundTrip(TestTemplate):
    def test_compose_get_update_send_records_call_order(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()

            # Gmail's create/update responses carry only a minimal message;
            # the services re-fetch at format=full, so get() is the source of
            # truth for the echoed state.
            mock.users().drafts().create().execute.return_value = {"id": "d-new"}
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-new",
                to="alice@example.com",
                subject="S",
                body="B-updated",
            )
            mock.users().drafts().update().execute.return_value = {"id": "d-new"}
            mock.users().drafts().send().execute.return_value = {
                "id": "msg-final",
                "threadId": "t-1",
            }

            patches = _patch_client(mock)
            _apply(patches)
            try:
                composed = gmail_compose(
                    GmailComposeInput(
                        user_id="alice",
                        to="alice@example.com",
                        subject="S",
                        body="B",
                    )
                )
                fetched = gmail_get_draft(
                    GmailGetDraftInput(user_id="alice", draft_id=composed.draft_id)
                )
                updated = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice",
                        draft_id=composed.draft_id,
                        body="B-updated",
                    )
                )
                sent = gmail_send(
                    GmailSendInput(user_id="alice", draft_id=composed.draft_id)
                )
            finally:
                _stop(patches)

        assert composed.draft_id == "d-new"
        assert fetched.draft_id == "d-new"
        assert updated.body == "B-updated"
        assert sent.message_id == "msg-final"


# ---------------------------------------------------------------------------
# Mark-read / Archive / Reply (Phase 6 additions)
# ---------------------------------------------------------------------------


class TestGmailMarkThreadRead(TestTemplate):
    def test_calls_threads_modify_with_remove_unread_label(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().modify().execute.return_value = {"id": "t-1"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_mark_thread_read(
                    GmailThreadModifyInput(user_id="alice", thread_id="t-1")
                )
            finally:
                _stop(patches)

        assert result.marked_read is True
        modify_calls = [
            c for c in mock.users().threads().modify.call_args_list if c.kwargs
        ]
        assert modify_calls, "threads().modify() was not called with kwargs"
        last = modify_calls[-1]
        assert last.kwargs["id"] == "t-1"
        assert last.kwargs["body"] == {"removeLabelIds": ["UNREAD"]}


class TestGmailArchiveThread(TestTemplate):
    def test_calls_threads_modify_with_remove_inbox_label(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().modify().execute.return_value = {"id": "t-2"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_archive_thread(
                    GmailThreadModifyInput(user_id="alice", thread_id="t-2")
                )
            finally:
                _stop(patches)

        assert result.archived is True
        modify_calls = [
            c for c in mock.users().threads().modify.call_args_list if c.kwargs
        ]
        assert modify_calls
        last = modify_calls[-1]
        assert last.kwargs["id"] == "t-2"
        assert last.kwargs["body"] == {"removeLabelIds": ["INBOX"]}


class TestGmailReplyToThread(TestTemplate):
    def _patch_reply(self, *, last_msg_headers: dict[str, str], created_draft: dict):
        mock = _make_mock_service()
        thread_payload = {
            "id": "t-rep",
            "messages": [
                {
                    "id": "m-last",
                    "internalDate": "1700000000000",
                    "payload": {"headers": _headers(last_msg_headers)},
                }
            ],
        }
        mock.users().threads().get().execute.return_value = thread_payload
        # Real Gmail returns only {id} from create; the service re-fetches the
        # saved draft at format=full for the echoed state.
        mock.users().drafts().create().execute.return_value = {
            "id": created_draft["id"]
        }
        mock.users().drafts().get().execute.return_value = created_draft
        return mock

    def test_derives_to_and_subject_from_last_message(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Original Subject",
                },
                created_draft=_draft_resource(
                    draft_id="d-rep",
                    to="sender@example.com",
                    subject="Re: Original Subject",
                    body="",
                    thread_id="t-rep",
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-rep"
        # Verify the MIME built carried derived To/Subject + the threadId was
        # threaded onto drafts().create.
        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        assert create_calls
        body = create_calls[-1].kwargs["body"]
        assert body["message"]["threadId"] == "t-rep"
        raw_b64 = body["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "sender@example.com"
        assert mime["Subject"] == "Re: Original Subject"

    def test_does_not_double_prefix_re_when_already_present(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Re: already a reply",
                },
                created_draft=_draft_resource(
                    draft_id="d-rep2",
                    to="sender@example.com",
                    subject="Re: already a reply",
                    body="",
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["Subject"] == "Re: already a reply"

    def test_raises_when_thread_has_no_messages(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = {
                "id": "t-empty",
                "messages": [],
            }
            patches = _patch_client(mock)
            _apply(patches)
            try:
                with pytest.raises(ValueError, match="no messages"):
                    gmail_reply_to_thread(
                        GmailReplyInput(user_id="alice", thread_id="t-empty")
                    )
            finally:
                _stop(patches)

    def _patch_multi_reply(self, *, messages: list[dict], created_draft: dict):
        """Mock a thread with several messages (thread order) + drafts.create."""
        mock = _make_mock_service()
        mock.users().threads().get().execute.return_value = {
            "id": "t-rep",
            "messages": messages,
        }
        # Real Gmail returns only {id} from create; the service re-fetches the
        # saved draft at format=full for the echoed state.
        mock.users().drafts().create().execute.return_value = {
            "id": created_draft["id"]
        }
        mock.users().drafts().get().execute.return_value = created_draft
        return mock

    @staticmethod
    def _thread_msg(msg_id: str, headers: dict[str, str]) -> dict:
        return {
            "id": msg_id,
            "internalDate": "1700000000000",
            "payload": {"headers": _headers(headers)},
        }

    def test_replies_to_other_party_when_owner_sent_last_message(self):
        # Seeded account is alice@example.com. Tom wrote first, then alice
        # replied - so the latest message is from self. The reply must default
        # to Tom, not alice, otherwise the owner emails themselves.
        with _patch_db() as factory:
            _seed_token(factory)  # alice@example.com
            mock = self._patch_multi_reply(
                messages=[
                    self._thread_msg(
                        "m-1",
                        {
                            "From": "Tom <tom@example.com>",
                            "To": "alice@example.com",
                            "Subject": "Question",
                        },
                    ),
                    self._thread_msg(
                        "m-2",
                        {
                            "From": "alice@example.com",
                            "To": "Tom <tom@example.com>",
                            "Subject": "Re: Question",
                        },
                    ),
                ],
                created_draft=_draft_resource(
                    draft_id="d-self", to="Tom <tom@example.com>", thread_id="t-rep"
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "Tom <tom@example.com>"

    def test_replies_to_recipients_when_all_messages_from_self(self):
        # The only message in the thread was sent by the owner. Reply to the
        # people it was addressed to (minus self), not the owner.
        with _patch_db() as factory:
            _seed_token(factory)  # alice@example.com
            mock = self._patch_multi_reply(
                messages=[
                    self._thread_msg(
                        "m-1",
                        {
                            "From": "alice@example.com",
                            "To": "Tom <tom@example.com>",
                            "Cc": "alice@example.com, Sue <sue@example.com>",
                            "Subject": "Heads up",
                        },
                    ),
                ],
                created_draft=_draft_resource(
                    draft_id="d-self2", to="Tom <tom@example.com>", thread_id="t-rep"
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        # Tom (To) and Sue (Cc) survive; alice (self, in Cc) is dropped.
        assert mime["To"] == "Tom <tom@example.com>, Sue <sue@example.com>"

    def test_raises_when_self_only_thread_has_no_other_recipient(self):
        # Whole thread is from the owner and the last message names no other
        # participant (To/Cc are only self) - there is nobody to reply to, so
        # the service raises instead of creating a blank-To draft.
        with _patch_db() as factory:
            _seed_token(factory)  # alice@example.com
            mock = self._patch_multi_reply(
                messages=[
                    self._thread_msg(
                        "m-1",
                        {
                            "From": "alice@example.com",
                            "To": "alice@example.com",
                            "Subject": "Note to self",
                        },
                    ),
                ],
                created_draft=_draft_resource(draft_id="d-none", thread_id="t-rep"),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                with pytest.raises(
                    ValueError, match="Cannot determine a reply recipient"
                ):
                    gmail_reply_to_thread(
                        GmailReplyInput(user_id="alice", thread_id="t-rep")
                    )
            finally:
                _stop(patches)

    def test_self_only_thread_still_works_when_caller_supplies_to(self):
        # The same self-only thread succeeds when the caller passes 'to'.
        with _patch_db() as factory:
            _seed_token(factory)  # alice@example.com
            mock = self._patch_multi_reply(
                messages=[
                    self._thread_msg(
                        "m-1",
                        {
                            "From": "alice@example.com",
                            "To": "alice@example.com",
                            "Subject": "Note to self",
                        },
                    ),
                ],
                created_draft=_draft_resource(
                    draft_id="d-ok", to="Tom <tom@example.com>", thread_id="t-rep"
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(
                        user_id="alice",
                        thread_id="t-rep",
                        to="Tom <tom@example.com>",
                    )
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "Tom <tom@example.com>"

    def test_caller_supplied_to_overrides_thread_default(self):
        # When the caller sets 'to' explicitly it is used verbatim - the
        # thread-derived default is not consulted.
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Original Subject",
                },
                created_draft=_draft_resource(draft_id="d-ov", thread_id="t-rep"),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(
                        user_id="alice",
                        thread_id="t-rep",
                        to="chosen@example.com",
                    )
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "chosen@example.com"

    def test_owner_sent_last_with_non_self_reply_to_is_still_skipped(self):
        # The owner (alice) sent the newest message and set a non-self Reply-To
        # (e.g. "reply to my assistant"). Ownership is judged by From, so this
        # message is still recognized as the owner's and skipped - the reply
        # goes to the earlier other party (Tom), NOT the Reply-To address.
        with _patch_db() as factory:
            _seed_token(factory)  # alice@example.com
            mock = self._patch_multi_reply(
                messages=[
                    self._thread_msg(
                        "m-1",
                        {
                            "From": "Tom <tom@example.com>",
                            "To": "alice@example.com",
                            "Subject": "Question",
                        },
                    ),
                    self._thread_msg(
                        "m-2",
                        {
                            "From": "alice@example.com",
                            "Reply-To": "assistant@other.com",
                            "To": "Tom <tom@example.com>",
                            "Subject": "Re: Question",
                        },
                    ),
                ],
                created_draft=_draft_resource(
                    draft_id="d-rt", to="Tom <tom@example.com>", thread_id="t-rep"
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "Tom <tom@example.com>"

    def test_caller_supplied_cc_and_bcc_pass_through(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Original Subject",
                },
                created_draft=_draft_resource(draft_id="d-cc", thread_id="t-rep"),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(
                        user_id="alice",
                        thread_id="t-rep",
                        cc="cc@example.com",
                        bcc="bcc@example.com",
                    )
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        # 'to' still defaults from the thread; cc/bcc are the caller's verbatim.
        assert mime["To"] == "sender@example.com"
        assert mime["Cc"] == "cc@example.com"
        assert mime["Bcc"] == "bcc@example.com"


# ---------------------------------------------------------------------------
# MCP-transport regression: omit vs null must survive FastMCP default-filling
# ---------------------------------------------------------------------------


def _mcp_kwargs(model_cls, provided: dict) -> dict:
    """Reproduce how FastMCP hands arguments to a tool.

    ``func_metadata.model_dump_one_level`` does ``getattr`` over *every*
    declared field, so an omitted parameter reaches the service filled with its
    default - never truly absent. This helper mirrors that: validate only the
    keys a caller actually sent, then read back every field. Building the input
    model from the result is exactly what the tool factory does on the wire.

    A model that relied on ``model_fields_set`` to tell omitted from null would
    have every field marked "set" here (the bug); the ``UNSET`` sentinel is the
    only thing that survives to say "omitted".
    """
    validated = model_cls.model_validate(provided)
    return {name: getattr(validated, name) for name in model_cls.model_fields}


class TestUpdateDraftOmitVsNullOverMcp(TestTemplate):
    """The reported bug: passing only to/cc over MCP wiped body and subject."""

    def _run(self, provided: dict, *, current_body="Original body"):
        original = _draft_resource(
            draft_id="d-1",
            to="orig@x",
            subject="Original Subject",
            body=current_body,
        )
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.side_effect = [original, original]
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                # Build the input the way the MCP transport does: every field
                # present, omitted ones filled with their (sentinel) default.
                gmail_update_draft(
                    GmailUpdateDraftInput(
                        **_mcp_kwargs(GmailUpdateDraftInput, provided)
                    )
                )
            finally:
                _stop(patches)
        raw = _last_update_raw(mock)
        return message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))

    def test_only_to_and_cc_preserves_body_and_subject(self):
        mime = self._run(
            {"user_id": "alice", "draft_id": "d-1", "to": "new@x", "cc": "cc@x"}
        )
        # The whole point of the report: omitted body/subject are NOT wiped.
        assert mime["To"] == "new@x"
        assert mime["Cc"] == "cc@x"
        assert mime["Subject"] == "Original Subject"
        decoded = mime.get_payload(decode=True)
        assert isinstance(decoded, bytes)
        assert "Original body" in decoded.decode("utf-8")

    def test_explicit_null_subject_over_mcp_still_clears(self):
        mime = self._run({"user_id": "alice", "draft_id": "d-1", "subject": None})
        # Explicit null is distinct from omitted: it clears.
        assert (mime["Subject"] or "") == ""
        assert mime["To"] == "orig@x"  # untouched


class TestMutationsEchoSavedState(TestTemplate):
    """Mutations must return the persisted draft, not the minimal API response."""

    def test_update_echoes_saved_state_not_minimal_response(self):
        original = _draft_resource(draft_id="d-1", to="a@x", subject="Subj", body="old")
        saved = _draft_resource(
            draft_id="d-1", to="a@x", subject="Subj", body="patched"
        )
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.side_effect = [original, saved]
            # Real Gmail returns only {id} here - the previous code echoed this
            # verbatim and produced an all-null draft.
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", body="patched"
                    )
                )
            finally:
                _stop(patches)
        assert draft.to == "a@x"
        assert draft.subject == "Subj"
        assert draft.body == "patched"
        assert draft.body_preview == "patched"

    def test_compose_echoes_saved_state_from_minimal_create_response(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().create().execute.return_value = {"id": "d-new"}
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-new", to="a@x", subject="S", body="B"
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_compose(
                    GmailComposeInput(user_id="alice", to="a@x", subject="S", body="B")
                )
            finally:
                _stop(patches)
        # Would be all-null if the minimal create response were echoed directly.
        assert draft.draft_id == "d-new"
        assert draft.to == "a@x"
        assert draft.body == "B"


# ---------------------------------------------------------------------------
# Non-destructive update + stable attachment handles (this change)
# ---------------------------------------------------------------------------


def _new_upload(filename: str = "new.txt", body: bytes = b"NEWFILE") -> AttachmentInput:
    return AttachmentInput(
        filename=filename,
        mime_type="text/plain",
        data_base64=base64.urlsafe_b64encode(body).decode("ascii"),
    )


class TestUpdateDraftPreservesAttachments(TestTemplate):
    """Acceptance: updating only the body leaves an existing attachment intact."""

    def test_omitting_attachments_reattaches_existing_file(self):
        original = _draft_resource_with_attachment(body="Original body")
        echoed = _draft_resource_with_attachment(body="New body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            # Pre-read returns the current draft; the post-update re-fetch
            # returns the saved state the response echoes.
            mock.users().drafts().get().execute.side_effect = [original, echoed]
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", body="New body"
                    )
                )
            finally:
                _stop(patches)

        # The raw MIME actually sent to Gmail still carries the file (proof the
        # bytes were re-downloaded and re-attached, not dropped).
        raw = _last_update_raw(mock)
        assert _attachment_filenames_in_raw(raw) == ["report.pdf"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["Subject"] == "hi"
        # The existing attachment bytes were re-fetched from the current message.
        mock.users().messages().attachments().get.assert_called()

        # Response echoes the live attachment metadata - no follow-up get needed.
        assert len(draft.attachments) == 1
        assert draft.attachments[0].filename == "report.pdf"
        assert draft.attachments[0].size_bytes == 1024
        assert draft.attachments[0].attachment_id == "att-1"
        assert draft.body_preview == "New body"

    def test_explicit_null_clears_attachments(self):
        original = _draft_resource_with_attachment(body="body")
        echoed = _draft_resource(draft_id="d-1", to="b@y", subject="hi", body="body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            # Pre-read still has the file; the post-update re-fetch reflects the
            # cleared attachment list.
            mock.users().drafts().get().execute.side_effect = [original, echoed]
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", attachments=None
                    )
                )
            finally:
                _stop(patches)

        raw = _last_update_raw(mock)
        assert _attachment_filenames_in_raw(raw) == []
        assert draft.attachments == []

    def test_explicit_null_clears_a_scalar_field(self):
        original = _draft_resource(
            draft_id="d-1", to="b@y", subject="Keep me", body="hi"
        )

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = original

            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(
                    GmailUpdateDraftInput(user_id="alice", draft_id="d-1", subject=None)
                )
            finally:
                _stop(patches)

        mime = message_from_bytes(
            base64.urlsafe_b64decode(_last_update_raw(mock).encode("ascii"))
        )
        # Cleared subject -> empty header; To preserved.
        assert (mime["Subject"] or "") == ""
        assert mime["To"] == "b@y"

    def test_attachment_by_reference_preserves_without_reupload(self):
        original = _draft_resource_with_attachment(body="body")
        echoed = _draft_resource_with_attachment(body="body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = echoed
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice",
                        draft_id="d-1",
                        body="body",
                        attachments=[AttachmentReference(attachment_id="att-1")],
                    )
                )
            finally:
                _stop(patches)

        raw = _last_update_raw(mock)
        assert _attachment_filenames_in_raw(raw) == ["report.pdf"]
        # Referenced by id -> bytes pulled from the existing message.
        get_call = mock.users().messages().attachments().get.call_args_list[-1]
        assert get_call.kwargs["id"] == "att-1"

    def test_reference_to_unknown_id_raises(self):
        original = _draft_resource_with_attachment(body="body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                with pytest.raises(ValueError, match="not on draft"):
                    gmail_update_draft(
                        GmailUpdateDraftInput(
                            user_id="alice",
                            draft_id="d-1",
                            attachments=[AttachmentReference(attachment_id="nope")],
                        )
                    )
            finally:
                _stop(patches)

    def test_mix_reference_and_new_upload(self):
        original = _draft_resource_with_attachment(body="body")
        echoed = _draft_resource_with_attachment(body="body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = echoed
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice",
                        draft_id="d-1",
                        attachments=[
                            AttachmentReference(attachment_id="att-1"),
                            _new_upload(filename="extra.txt"),
                        ],
                    )
                )
            finally:
                _stop(patches)

        raw = _last_update_raw(mock)
        assert sorted(_attachment_filenames_in_raw(raw)) == ["extra.txt", "report.pdf"]


class TestEditBodyRepeatedlyKeepsAttachment(TestTemplate):
    """Acceptance: change the body three times without re-uploading or losing the file."""

    def test_three_body_edits_in_a_row(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            # The server always reports the file still on the draft.
            mock.users().drafts().get().execute.return_value = (
                _draft_resource_with_attachment(body="v0")
            )
            mock.users().drafts().update().execute.return_value = (
                _draft_resource_with_attachment(body="vN")
            )
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            drafts = []
            try:
                for new_body in ("v1", "v2", "v3"):
                    drafts.append(
                        gmail_update_draft(
                            GmailUpdateDraftInput(
                                user_id="alice", draft_id="d-1", body=new_body
                            )
                        )
                    )
                    # Each round re-attaches the file in the outgoing MIME.
                    assert _attachment_filenames_in_raw(_last_update_raw(mock)) == [
                        "report.pdf"
                    ]
            finally:
                _stop(patches)

        # Three edits happened, and the file is still present at the end -
        # without ever supplying attachment bytes.
        assert len(drafts) == 3
        assert len(drafts[-1].attachments) == 1
        assert drafts[-1].attachments[0].filename == "report.pdf"


class TestAddRemoveAttachment(TestTemplate):
    def test_add_attachment_appends_and_keeps_content(self):
        original = _draft_resource_with_attachment(
            body="Keep this body", subject="Keep subj", to="keep@x"
        )
        echoed = _draft_resource_with_attachment(body="Keep this body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = echoed
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_add_attachment(
                    GmailAddAttachmentInput(
                        user_id="alice",
                        draft_id="d-1",
                        attachment=_new_upload(filename="added.txt"),
                    )
                )
            finally:
                _stop(patches)

        raw = _last_update_raw(mock)
        # Both the existing and the newly-added file are present.
        assert sorted(_attachment_filenames_in_raw(raw)) == ["added.txt", "report.pdf"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        # Content fields untouched.
        assert mime["To"] == "keep@x"
        assert mime["Subject"] == "Keep subj"
        body_part = next(p for p in mime.walk() if p.get_content_type() == "text/plain")
        decoded = body_part.get_payload(decode=True)
        assert isinstance(decoded, bytes)
        assert "Keep this body" in decoded.decode("utf-8")
        assert result.draft_id == "d-1"
        assert isinstance(result.attachments, list)

    def test_remove_attachment_drops_only_that_file(self):
        original = _draft_resource_with_attachment(
            body="Body stays", subject="Subj stays", to="stay@x"
        )
        echoed = _draft_resource(
            draft_id="d-1", to="stay@x", subject="Subj stays", body="Body stays"
        )

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            # Pre-read has the file (so it can be found and removed); the
            # post-update re-fetch reflects the file being gone.
            mock.users().drafts().get().execute.side_effect = [original, echoed]
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )

            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_remove_attachment(
                    GmailRemoveAttachmentInput(
                        user_id="alice", draft_id="d-1", attachment_id="att-1"
                    )
                )
            finally:
                _stop(patches)

        raw = _last_update_raw(mock)
        assert _attachment_filenames_in_raw(raw) == []
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["To"] == "stay@x"
        assert mime["Subject"] == "Subj stays"
        assert result.attachments == []

    def test_remove_unknown_attachment_raises(self):
        original = _draft_resource_with_attachment(body="body")

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original

            patches = _patch_client(mock)
            _apply(patches)
            try:
                with pytest.raises(ValueError, match="not on draft"):
                    gmail_remove_attachment(
                        GmailRemoveAttachmentInput(
                            user_id="alice", draft_id="d-1", attachment_id="ghost"
                        )
                    )
            finally:
                _stop(patches)


# ---------------------------------------------------------------------------
# Rebuild preserves content beyond plain body (cubic review fixes)
# ---------------------------------------------------------------------------


def _draft_with_headers(
    headers: dict[str, str],
    *,
    body: str = "hi",
    draft_id: str = "d-1",
    thread_id: str = "t-1",
) -> dict:
    return {
        "id": draft_id,
        "message": _plain_message(
            message_id=f"m-{draft_id}",
            thread_id=thread_id,
            headers=headers,
            body=body,
        ),
    }


def _draft_resource_html_only(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    html: str = "<p>hello</p>",
    thread_id: str = "t-1",
) -> dict:
    return {
        "id": draft_id,
        "message": {
            "id": f"m-{draft_id}",
            "threadId": thread_id,
            "snippet": "hello",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/html",
                "headers": _headers({"To": to, "Subject": subject}),
                "body": {"data": _b64url(html), "size": len(html)},
            },
        },
    }


def _mime_part_texts(raw_b64: str, content_type: str) -> list[str]:
    mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
    out = []
    for p in mime.walk():
        if p.get_content_type() == content_type:
            payload = p.get_payload(decode=True)
            if isinstance(payload, bytes):
                out.append(payload.decode("utf-8"))
    return out


class TestRebuildPreservesContent(TestTemplate):
    def _run_update(self, original, update_input, echoed=None):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = echoed or original
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(update_input)
            finally:
                _stop(patches)
        return _last_update_raw(mock)

    def test_omitted_body_preserves_html_only_body(self):
        original = _draft_resource_html_only(html="<p>keep me</p>")
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", subject="New subj"),
        )
        # The HTML body survived; it was not flattened to an empty plain body.
        html_parts = _mime_part_texts(raw, "text/html")
        assert any("keep me" in h for h in html_parts)

    def test_setting_body_replaces_with_plain_text(self):
        original = _draft_resource_html_only(html="<p>old html</p>")
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", body="brand new"),
        )
        plain = _mime_part_texts(raw, "text/plain")
        assert any("brand new" in p for p in plain)
        assert not any("old html" in h for h in _mime_part_texts(raw, "text/html"))

    def test_omitted_bcc_is_preserved(self):
        original = _draft_with_headers(
            {"To": "a@x", "Subject": "S", "Bcc": "secret@x"}, body="b"
        )
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", body="new body"),
        )
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["Bcc"] == "secret@x"

    def test_explicit_null_clears_bcc(self):
        original = _draft_with_headers(
            {"To": "a@x", "Subject": "S", "Bcc": "secret@x"}, body="b"
        )
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", bcc=None),
        )
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["Bcc"] is None

    def test_reply_threading_headers_preserved(self):
        original = _draft_with_headers(
            {
                "To": "a@x",
                "Subject": "Re: S",
                "In-Reply-To": "<parent@m>",
                "References": "<root@m> <parent@m>",
            },
            body="b",
        )
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", body="edited"),
        )
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["In-Reply-To"] == "<parent@m>"
        assert mime["References"] == "<root@m> <parent@m>"

    def test_inline_attachment_without_id_does_not_crash(self):
        # A named part with a size but no attachmentId and no inline data:
        # it has no retrievable bytes, so a body-only edit must skip it, not
        # call the attachments API with an empty id and error.
        original = {
            "id": "d-1",
            "message": {
                "id": "m-d-1",
                "threadId": "t-1",
                "snippet": "s",
                "internalDate": "1700000000000",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": _headers({"To": "a@x", "Subject": "S"}),
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64url("body here"), "size": 9},
                        },
                        {
                            "mimeType": "application/pdf",
                            "filename": "weird.pdf",
                            "body": {"size": 10},  # no attachmentId, no data
                        },
                    ],
                },
            },
        }
        raw = self._run_update(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", body="new"),
        )
        # No crash; the un-retrievable part is dropped rather than erroring.
        assert _attachment_filenames_in_raw(raw) == []
        assert any("new" in p for p in _mime_part_texts(raw, "text/plain"))


class TestAddAttachmentPreservesHtmlAndBcc(TestTemplate):
    def test_add_attachment_keeps_html_body_and_bcc(self):
        original = {
            "id": "d-1",
            "message": {
                "id": "m-d-1",
                "threadId": "t-1",
                "snippet": "s",
                "internalDate": "1700000000000",
                "payload": {
                    "mimeType": "text/html",
                    "headers": _headers(
                        {"To": "a@x", "Subject": "S", "Bcc": "secret@x"}
                    ),
                    "body": {"data": _b64url("<p>html body</p>"), "size": 16},
                },
            },
        }
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = original
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_add_attachment(
                    GmailAddAttachmentInput(
                        user_id="alice",
                        draft_id="d-1",
                        attachment=_new_upload(filename="a.txt"),
                    )
                )
            finally:
                _stop(patches)
        raw = _last_update_raw(mock)
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        assert mime["Bcc"] == "secret@x"
        assert _attachment_filenames_in_raw(raw) == ["a.txt"]
        html = _mime_part_texts(raw, "text/html")
        assert any("html body" in h for h in html)


class TestAppToolAttachmentCoercion(TestTemplate):
    def test_reference_dict_becomes_attachment_reference(self):
        out = _coerce_attachments([{"attachment_id": "att-1"}])
        assert out is not None
        assert isinstance(out[0], AttachmentReference)
        assert out[0].attachment_id == "att-1"

    def test_upload_dict_becomes_attachment_input(self):
        out = _coerce_attachments(
            [
                {
                    "filename": "f.txt",
                    "mime_type": "text/plain",
                    "data_base64": base64.urlsafe_b64encode(b"x").decode("ascii"),
                }
            ]
        )
        assert out is not None
        assert isinstance(out[0], AttachmentInput)

    def test_mixed_list_routes_each_item(self):
        out = _coerce_attachments(
            [
                {"attachment_id": "att-1"},
                {
                    "filename": "f.txt",
                    "mime_type": "text/plain",
                    "data_base64": base64.urlsafe_b64encode(b"x").decode("ascii"),
                },
            ]
        )
        assert out is not None
        assert isinstance(out[0], AttachmentReference)
        assert isinstance(out[1], AttachmentInput)

    def test_empty_attachment_dict_raises(self):
        with pytest.raises(ValueError, match="data_base64"):
            _coerce_attachments([{}])

    def test_unset_passes_through_to_preserve(self):
        # The composer autosave omits attachments; UNSET must survive the patch
        # coercion so the update preserves existing files instead of clearing.
        assert _patch_attachments(UNSET) is UNSET
        # None/[] still clear; a list still coerces to models.
        assert _patch_attachments(None) is None
        assert _patch_attachments([]) is None


class TestComposerAutosavePreservesAttachments(TestTemplate):
    """save_draft/send omit attachments on autosave - files must NOT be dropped."""

    def _run_save_draft(self, **kwargs):
        original = _draft_resource_with_attachment(body="draft body")
        echoed = _draft_resource_with_attachment(body="draft body")
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.side_effect = [original, echoed]
            mock.users().drafts().update().execute.return_value = {"id": "d-1"}
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                # Call the app-tool exactly as the composer does: text fields
                # only, attachments omitted entirely.
                result = _save_draft(draft_id="d-1", user_id="alice", **kwargs)
            finally:
                _stop(patches)
        return result, _last_update_raw(mock)

    def test_autosave_without_attachments_keeps_existing_file(self):
        result, raw = self._run_save_draft(
            to="a@x", cc="", bcc="", subject="Subj", body="edited body"
        )
        # The re-attached file survives in the outgoing MIME...
        assert _attachment_filenames_in_raw(raw) == ["report.pdf"]
        # ...and the echoed draft still reports it.
        assert [a.filename for a in result.attachments] == ["report.pdf"]

    def test_explicit_empty_list_still_clears(self):
        # Distinct from omission: an explicit [] means "drop them all".
        _result, raw = self._run_save_draft(
            to="a@x", subject="S", body="b", attachments=[]
        )
        assert _attachment_filenames_in_raw(raw) == []


class TestUpdateInputJsonSerializable(TestTemplate):
    """UNSET must not make the patch model unserializable (idempotency/logging)."""

    def test_partial_update_dumps_to_json_without_crashing(self):
        m = GmailUpdateDraftInput(draft_id="d-1", to="a@b")
        dumped = m.model_dump(mode="json")
        # Omitted fields collapse to null on the wire; provided fields verbatim.
        assert dumped["to"] == "a@b"
        assert dumped["subject"] is None
        assert dumped["body"] is None
        # Round-trips through JSON (what the idempotency store does).
        assert json.loads(json.dumps(dumped))["to"] == "a@b"

    def test_update_draft_is_mutating_and_survives_idempotency_dump(self):
        # gmail_update_draft patches server state, so its API route is
        # idempotency-guarded like compose/reply/send. That guard dumps the
        # request body via model_dump(mode="json") - which only works because
        # UNSET is serializable (above). Lock both facts together.
        entry = next(e for e in get_registry() if e.name == "gmail_update_draft")
        assert entry.mutating is True
        partial = GmailUpdateDraftInput(draft_id="d-1", body="just the body")
        # Exactly what api_server.idempotency.execute_idempotent stores.
        assert partial.model_dump(mode="json")["body"] == "just the body"


# ---------------------------------------------------------------------------
# Inline (cid:) image preservation on rebuild
# ---------------------------------------------------------------------------


def _draft_resource_html_inline_image(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    img_cid: str = "img1",
    img_bytes: bytes = b"PNGDATA",
    thread_id: str = "t-1",
) -> dict:
    html = f'<p>see <img src="cid:{img_cid}"></p>'
    return {
        "id": draft_id,
        "message": {
            "id": f"m-{draft_id}",
            "threadId": thread_id,
            "snippet": "see",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "multipart/related",
                "headers": _headers({"To": to, "Subject": subject}),
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64url(html), "size": len(html)},
                    },
                    {
                        "mimeType": "image/png",
                        "headers": _headers({"Content-ID": f"<{img_cid}>"}),
                        "body": {
                            "data": base64.urlsafe_b64encode(img_bytes).decode("ascii"),
                            "size": len(img_bytes),
                        },
                    },
                ],
            },
        },
    }


def _parts_by_type(raw_b64: str, content_type: str):
    mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
    return [p for p in mime.walk() if p.get_content_type() == content_type]


class TestInlineImagePreservation(TestTemplate):
    def _run(self, original, update_input):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = original
            mock.users().messages().attachments().get().execute.return_value = (
                _gmail_attachment_blob()
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(update_input)
            finally:
                _stop(patches)
        return _last_update_raw(mock)

    def test_omitted_body_preserves_inline_image(self):
        original = _draft_resource_html_inline_image(img_bytes=b"PNGDATA")
        raw = self._run(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", subject="New subj"),
        )
        imgs = _parts_by_type(raw, "image/png")
        assert len(imgs) == 1
        assert imgs[0].get("Content-ID") == "<img1>"
        assert imgs[0].get_payload(decode=True) == b"PNGDATA"
        # The HTML body and its cid reference are intact.
        assert any("cid:img1" in h for h in _mime_part_texts(raw, "text/html"))

    def test_setting_plain_body_drops_orphaned_inline_image(self):
        original = _draft_resource_html_inline_image()
        raw = self._run(
            original,
            GmailUpdateDraftInput(user_id="alice", draft_id="d-1", body="plain now"),
        )
        # HTML is gone, so the now-orphaned inline image is dropped too.
        assert _parts_by_type(raw, "image/png") == []
        assert any("plain now" in p for p in _mime_part_texts(raw, "text/plain"))

    def test_add_attachment_keeps_inline_image(self):
        original = _draft_resource_html_inline_image(img_bytes=b"PNGDATA")
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = original
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_add_attachment(
                    GmailAddAttachmentInput(
                        user_id="alice",
                        draft_id="d-1",
                        attachment=_new_upload(filename="doc.txt"),
                    )
                )
            finally:
                _stop(patches)
        raw = _last_update_raw(mock)
        imgs = _parts_by_type(raw, "image/png")
        assert len(imgs) == 1
        assert imgs[0].get("Content-ID") == "<img1>"
        assert imgs[0].get_payload(decode=True) == b"PNGDATA"
        # The newly-added file is a normal (mixed) attachment, not inline.
        assert _attachment_filenames_in_raw(raw) == ["doc.txt"]


def _draft_alt_with_inline_image(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    img_cid: str = "img1",
    img_bytes: bytes = b"PNGDATA",
    plain: str = "plain fallback",
    thread_id: str = "t-1",
) -> dict:
    """A draft with BOTH plain + HTML alternatives and an inline cid: image."""
    html = f'<p>see <img src="cid:{img_cid}"></p>'
    return {
        "id": draft_id,
        "message": {
            "id": f"m-{draft_id}",
            "threadId": thread_id,
            "snippet": "see",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": _headers({"To": to, "Subject": subject}),
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url(plain), "size": len(plain)},
                    },
                    {
                        "mimeType": "multipart/related",
                        "parts": [
                            {
                                "mimeType": "text/html",
                                "body": {"data": _b64url(html), "size": len(html)},
                            },
                            {
                                "mimeType": "image/png",
                                "headers": _headers({"Content-ID": f"<{img_cid}>"}),
                                "body": {
                                    "data": base64.urlsafe_b64encode(img_bytes).decode(
                                        "ascii"
                                    ),
                                    "size": len(img_bytes),
                                },
                            },
                        ],
                    },
                ],
            },
        },
    }


class TestInlineImageAlternativeStructure(TestTemplate):
    def test_plain_and_html_inline_image_nests_related_under_alternative(self):
        original = _draft_alt_with_inline_image(img_bytes=b"PNGDATA")
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = original
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", subject="New subj"
                    )
                )
            finally:
                _stop(patches)
        raw = _last_update_raw(mock)
        mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
        # Canonical, broadly-compatible shape: the related (html + image) nests
        # INSIDE the alternative, not the other way around.
        assert mime.get_content_type() == "multipart/alternative"
        related = [
            p for p in mime.walk() if p.get_content_type() == "multipart/related"
        ]
        assert len(related) == 1
        related_types = [p.get_content_type() for p in related[0].walk()]
        assert related_types == ["multipart/related", "text/html", "image/png"]
        # Plain alternative is a sibling of the related, not nested inside it.
        all_types = [p.get_content_type() for p in mime.walk()]
        assert "text/plain" in all_types
        assert "text/plain" not in related_types
        # Image bytes and cid intact.
        img = _parts_by_type(raw, "image/png")[0]
        assert img.get("Content-ID") == "<img1>"
        assert img.get_payload(decode=True) == b"PNGDATA"
