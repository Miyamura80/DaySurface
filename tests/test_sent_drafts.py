"""The server's sent-draft record tells a lost send reply from a discard.

Gmail deletes a draft on send and on discard alike, so without the record a
composer whose send reply was lost could only guess, and a retry failed with
"draft gone" even though the email went out.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError
from sqlalchemy.exc import OperationalError

from db import engine as db_engine
from db.models.sent_drafts import SentDraft
from mcp_server.app_tools.gmail_composer import send, send_status
from models.gmail import GmailSendInput, GmailSendResult
from services.gmail_draft_helpers import DraftGoneError
from services.gmail_drafts_svc import (
    SENT_RECORD_RETENTION,
    find_sent,
    gmail_send,
    record_sent,
)
from tests.gmail_fakes import (
    draft_resource,
    make_mock_service,
    patch_client,
    patch_db,
    seed_token,
    start_patches,
    stop_patches,
)
from tests.test_template import TestTemplate


@contextmanager
def _gmail(svc: object):
    with patch_db() as factory:
        seed_token(factory)
        patches = patch_client(svc)
        start_patches(patches)
        try:
            yield
        finally:
            stop_patches(patches)


def _gone() -> HttpError:
    return HttpError(httplib2.Response({"status": 404}), b"{}")


def _sending_mailbox(*, draft_live: bool = False) -> MagicMock:
    """Sends once, then answers 404 like Gmail does for the deleted draft.

    ``draft_live`` lets the composer's pre-send save find the draft first.
    """
    mock = make_mock_service()
    drafts = mock.users().drafts()
    drafts.send().execute.configure_mock(
        side_effect=[{"id": "m1", "threadId": "t1"}, _gone()]
    )
    if draft_live:
        drafts.get().execute.return_value = draft_resource()
        drafts.update().execute.return_value = draft_resource()
    else:
        drafts.get().execute.configure_mock(side_effect=_gone())
    return mock


def _send() -> GmailSendResult:
    return gmail_send(GmailSendInput(user_id="alice", draft_id="d"))


def _result(message_id: str = "m1", sent_at: datetime | None = None):
    return GmailSendResult(
        message_id=message_id, thread_id="t1", sent_at=sent_at or datetime.now(UTC)
    )


class TestSentDraftRecord(TestTemplate):
    def test_a_send_is_recorded(self):
        with _gmail(_sending_mailbox()):
            _send()
            recorded = find_sent("alice", "d")
        assert recorded is not None
        assert (recorded.message_id, recorded.thread_id) == ("m1", "t1")
        assert recorded.sent_at.tzinfo is not None

    def test_the_record_is_per_user(self):
        with _gmail(_sending_mailbox()):
            _send()
            assert find_sent("bob", "d") is None

    def test_a_repeat_send_returns_the_first_result(self):
        # Also the losing side of two concurrent sends: Gmail answers 404.
        with _gmail(_sending_mailbox()):
            first = _send()
            assert _send() == first

    def test_a_gone_draft_with_no_record_still_fails(self):
        mock = make_mock_service()
        mock.users().drafts().send().execute.configure_mock(side_effect=_gone())
        with _gmail(mock), pytest.raises(DraftGoneError):
            _send()

    def test_a_failed_record_write_still_reports_the_send(self):
        # The send already happened; reporting it failed would be worse than
        # losing the record.
        with (
            _gmail(_sending_mailbox()),
            patch(
                "services.gmail_drafts_svc.use_db_session",
                side_effect=OperationalError("insert", {}, Exception("db down")),
            ),
        ):
            assert _send().message_id == "m1"

    def test_a_second_write_keeps_the_first_send(self):
        with patch_db():
            record_sent("alice", "d", _result("m1"))
            record_sent("alice", "d", _result("m2"))
            recorded = find_sent("alice", "d")
        assert recorded is not None and recorded.message_id == "m1"

    def test_writing_prunes_expired_records(self):
        stale = datetime.now(UTC) - SENT_RECORD_RETENTION - timedelta(hours=1)
        with patch_db():
            record_sent("alice", "old", _result(sent_at=stale))
            record_sent("alice", "new", _result())
            with db_engine.use_db_session() as session:
                kept = {row.draft_id for row in session.query(SentDraft)}
        assert kept == {"new"}


class TestComposerUsesTheRecord(TestTemplate):
    def test_send_status_reports_the_recorded_send(self):
        with _gmail(_sending_mailbox()):
            _send()
            status = send_status(draft_id="d", user_id="alice")
        assert status.state == "sent"
        assert status.result is not None and status.result.message_id == "m1"

    def test_a_discarded_draft_reads_as_gone(self):
        with _gmail(_sending_mailbox()):
            status = send_status(draft_id="d", user_id="alice")
        assert (status.state, status.result) == ("gone", None)

    def test_a_retried_composer_send_returns_the_first_send(self):
        mock = _sending_mailbox(draft_live=True)
        with _gmail(mock):
            first = send(draft_id="d", user_id="alice", body="hi")
            # Retry after a lost reply: the draft is gone, so the save fails
            # first; the record answers for it.
            mock.users().drafts().update().execute.configure_mock(side_effect=_gone())
            second = send(draft_id="d", user_id="alice", body="hi")
        assert second == first

    def test_a_composer_send_of_a_discarded_draft_says_so(self):
        mock = _sending_mailbox()
        mock.users().drafts().update().execute.configure_mock(side_effect=_gone())
        with _gmail(mock), pytest.raises(ValueError, match="no longer exists"):
            send(draft_id="d", user_id="alice", body="hi")
