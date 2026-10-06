"""The model learns when the user sent or discarded a draft from the composer.

The composer's tools are app-only, so the model never sees those calls. These
guards cover what it does next: touching a draft that is gone, or drafting a
reply the user already sent.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from models.gmail import (
    AttachmentInput,
    GmailAddAttachmentInput,
    GmailDiscardDraftInput,
    GmailGetDraftInput,
    GmailReplyInput,
    GmailSendInput,
    GmailUpdateDraftInput,
)
from services.gmail_attachments_svc import gmail_add_attachment
from services.gmail_draft_helpers import DraftGoneError
from services.gmail_drafts_svc import (
    gmail_discard_draft,
    gmail_get_draft,
    gmail_reply_to_thread,
    gmail_send,
    gmail_update_draft,
)
from tests.test_gmail_services import (
    _apply,
    _draft_resource,
    _headers,
    _make_mock_service,
    _patch_client,
    _patch_db,
    _seed_token,
    _stop,
)
from tests.test_template import TestTemplate


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


@contextmanager
def _gmail(mock: MagicMock):
    with _patch_db() as factory:
        _seed_token(factory)
        patches = _patch_client(mock)
        _apply(patches)
        try:
            yield
        finally:
            _stop(patches)


def _gone_mailbox() -> MagicMock:
    mock = _make_mock_service()
    for call in ("get", "send", "update", "delete"):
        getattr(mock.users().drafts(), call)().execute.configure_mock(
            side_effect=_http_error(404)
        )
    return mock


class TestDraftGone(TestTemplate):
    @pytest.mark.parametrize(
        "call",
        [
            lambda: gmail_get_draft(GmailGetDraftInput(user_id="alice", draft_id="d")),
            lambda: gmail_update_draft(
                GmailUpdateDraftInput(user_id="alice", draft_id="d", body="x")
            ),
            lambda: gmail_send(GmailSendInput(user_id="alice", draft_id="d")),
            lambda: gmail_discard_draft(
                GmailDiscardDraftInput(user_id="alice", draft_id="d")
            ),
            lambda: gmail_add_attachment(
                GmailAddAttachmentInput(
                    user_id="alice",
                    draft_id="d",
                    attachment=AttachmentInput(
                        filename="a.txt", mime_type="text/plain", data_base64="eA=="
                    ),
                )
            ),
        ],
        ids=["get", "update", "send", "discard", "add_attachment"],
    )
    def test_a_sent_draft_reads_as_already_sent(self, call):
        with _gmail(_gone_mailbox()), pytest.raises(DraftGoneError) as caught:
            call()
        assert "already sent or discarded" in str(caught.value)
        assert "'d'" in str(caught.value)

    def test_other_errors_pass_through(self):
        mock = _make_mock_service()
        mock.users().drafts().send().execute.configure_mock(
            side_effect=_http_error(500)
        )
        with _gmail(mock), pytest.raises(HttpError):
            gmail_send(GmailSendInput(user_id="alice", draft_id="d"))


def _msg(mid: str, labels: list[str], sender: str) -> dict:
    return {
        "id": mid,
        "labelIds": labels,
        "internalDate": "1700000000000",
        "payload": {"headers": _headers({"From": sender, "Subject": "Plans"})},
    }


_THEIRS = ["INBOX"]
_MINE = ["SENT"]


def _reply(messages: list[dict], **kwargs) -> MagicMock:
    mock = _make_mock_service()
    mock.users().threads().get().execute.return_value = {
        "id": "t",
        "messages": messages,
    }
    mock.users().drafts().create().execute.return_value = {"id": "d-new"}
    mock.users().drafts().get().execute.return_value = _draft_resource(
        draft_id="d-new", thread_id="t"
    )
    with _gmail(mock):
        gmail_reply_to_thread(
            GmailReplyInput(user_id="alice", thread_id="t", to="bob@x", **kwargs)
        )
    return mock


def _created(mock: MagicMock) -> bool:
    return any(c.kwargs for c in mock.users().drafts().create.call_args_list)


class TestDuplicateReplyGuard(TestTemplate):
    def test_refuses_when_the_user_already_replied(self):
        thread = [_msg("m1", _THEIRS, "bob@x"), _msg("m2", _MINE, "alice@x")]
        with pytest.raises(ValueError, match="already replied") as caught:
            _reply(thread)
        assert "follow_up=true" in str(caught.value)
        assert "2023-11-14 22:13 UTC" in str(caught.value)

    def test_follow_up_overrides(self):
        thread = [_msg("m1", _THEIRS, "bob@x"), _msg("m2", _MINE, "alice@x")]
        assert _created(_reply(thread, follow_up=True))

    @pytest.mark.parametrize(
        "thread",
        [
            # They wrote back after the user's reply.
            [
                _msg("m1", _THEIRS, "bob@x"),
                _msg("m2", _MINE, "alice@x"),
                _msg("m3", _THEIRS, "bob@x"),
            ],
            # The user's message is an unsent draft.
            [_msg("m1", _THEIRS, "bob@x"), _msg("m2", ["DRAFT"], "alice@x")],
            # Only the user ever wrote: a reply is a deliberate nudge.
            [_msg("m1", _MINE, "alice@x"), _msg("m2", _MINE, "alice@x")],
            # A note to self lands in INBOX and counts as incoming.
            [_msg("m1", _THEIRS, "bob@x"), _msg("m2", _MINE + _THEIRS, "alice@x")],
        ],
        ids=["they_replied", "draft", "own_thread", "note_to_self"],
    )
    def test_drafts_when_the_reply_is_not_a_duplicate(self, thread):
        assert _created(_reply(thread))
