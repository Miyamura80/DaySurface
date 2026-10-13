"""The server's sent-draft record tells a lost send reply from a discard.

Gmail deletes a draft on send and on discard alike, so without the record a
composer whose send reply was lost could only guess, and a retry failed with
"draft gone" even though the email went out.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httplib2
from googleapiclient.errors import HttpError
from sqlalchemy.exc import OperationalError

from mcp_server.app_tools.gmail_composer import send, send_status
from models.gmail import GmailSendInput, GmailSendResult
from services.gmail_drafts_svc import find_sent, gmail_send, record_sent
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


def _send() -> object:
    return gmail_send(GmailSendInput(user_id="alice", draft_id="d"))


class TestSentDraftRecord(TestTemplate):
    def test_a_send_is_recorded(self):
        with _gmail(_sending_mailbox()):
            _send()
            recorded = find_sent("alice", "d")
        assert recorded is not None
        assert (recorded.message_id, recorded.thread_id) == ("m1", "t1")

    def test_the_record_is_per_user(self):
        with _gmail(_sending_mailbox()):
            _send()
            assert find_sent("bob", "d") is None

    def test_a_repeat_send_returns_the_first_result(self):
        mock = _sending_mailbox()
        with _gmail(mock):
            first = _send()
            second = _send()
        assert second == first
        # The repeat never reaches Gmail, whose draft is already gone.
        assert mock.users().drafts().send().execute.call_count == 1

    def test_a_failed_record_write_is_swallowed(self):
        # The send already happened; reporting it failed would be worse than
        # losing the record.
        result = GmailSendResult(
            message_id="m1", thread_id="t1", sent_at=datetime.now(UTC)
        )
        with patch(
            "services.gmail_drafts_svc.use_db_session",
            side_effect=OperationalError("insert", {}, Exception("db down")),
        ):
            record_sent("alice", "d", result)


class TestComposerUsesTheRecord(TestTemplate):
    def test_send_status_reports_the_recorded_send(self):
        with _gmail(_sending_mailbox()):
            _send()
            status = send_status(draft_id="d", user_id="alice")
        assert status.draft_exists is False
        assert status.sent is not None and status.sent.message_id == "m1"

    def test_a_discarded_draft_has_no_sent_record(self):
        with _gmail(_sending_mailbox()):
            status = send_status(draft_id="d", user_id="alice")
        assert status.draft_exists is False
        assert status.sent is None

    def test_a_retried_composer_send_returns_the_first_send(self):
        mock = _sending_mailbox(draft_live=True)
        with _gmail(mock):
            first = send(draft_id="d", user_id="alice", body="hi")
            # Retry after a lost reply: the draft is gone, so saving it would
            # fail. The record answers before the save is attempted.
            mock.users().drafts().update().execute.configure_mock(side_effect=_gone())
            second = send(draft_id="d", user_id="alice", body="hi")
        assert second == first
