"""The model learns when the user sent or discarded a draft from the composer.

The composer's tools are app-only, so the model never sees those calls. These
guards cover what it does next: touching a draft that is gone, or drafting a
reply the user already sent.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httplib2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from api_server.middleware.error_handler import client_refusal_handler
from models.gmail import (
    AttachmentInput,
    GmailAddAttachmentInput,
    GmailDiscardDraftInput,
    GmailGetDraftInput,
    GmailReplyInput,
    GmailSendInput,
    GmailUpdateDraftInput,
)
from services import ClientRefusalError
from services._gmail_fake_backend import _FakeGmailResource
from services.gmail_attachments_svc import gmail_add_attachment
from services.gmail_draft_helpers import DraftGoneError
from services.gmail_drafts_svc import (
    gmail_discard_draft,
    gmail_get_draft,
    gmail_send,
    gmail_update_draft,
)
from services.gmail_reply_svc import DuplicateReplyError, gmail_reply_to_thread
from tests.gmail_fakes import (
    draft_resource,
    header_list,
    make_mock_service,
    patch_client,
    patch_db,
    seed_token,
    start_patches,
    stop_patches,
)
from tests.test_template import TestTemplate


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


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


def _gone_mailbox() -> MagicMock:
    mock = make_mock_service()
    for call in ("get", "send", "update", "delete"):
        getattr(mock.users().drafts(), call)().execute.configure_mock(
            side_effect=_http_error(404)
        )
    return mock


_CALLS_ON_A_GONE_DRAFT = [
    lambda: gmail_get_draft(GmailGetDraftInput(user_id="alice", draft_id="d")),
    lambda: gmail_update_draft(
        GmailUpdateDraftInput(user_id="alice", draft_id="d", body="x")
    ),
    lambda: gmail_send(GmailSendInput(user_id="alice", draft_id="d")),
    lambda: gmail_add_attachment(
        GmailAddAttachmentInput(
            user_id="alice",
            draft_id="d",
            attachment=AttachmentInput(
                filename="a.txt", mime_type="text/plain", data_base64="eA=="
            ),
        )
    ),
]
_CALL_IDS = ["get", "update", "send", "add_attachment"]


class TestDraftGone(TestTemplate):
    @pytest.mark.parametrize("call", _CALLS_ON_A_GONE_DRAFT, ids=_CALL_IDS)
    def test_a_missing_draft_says_sent_discarded_or_wrong_id(self, call):
        with _gmail(_gone_mailbox()), pytest.raises(DraftGoneError) as caught:
            call()
        text = str(caught.value)
        assert "'d' was not found" in text
        assert "already sent or discarded" in text
        assert "the id is wrong" in text
        assert "gmail_list_drafts" in text

    @pytest.mark.parametrize("call", _CALLS_ON_A_GONE_DRAFT, ids=_CALL_IDS)
    def test_the_fake_backend_answers_like_gmail(self, call):
        with _gmail(_FakeGmailResource()), pytest.raises(DraftGoneError):
            call()

    def test_discarding_a_gone_draft_succeeds(self):
        for svc in (_gone_mailbox(), _FakeGmailResource()):
            with _gmail(svc):
                result = gmail_discard_draft(
                    GmailDiscardDraftInput(user_id="alice", draft_id="d")
                )
            assert result.discarded is True

    def test_a_draft_gone_mid_edit_reads_the_same(self):
        # The draft was there for the first read, then sent before its
        # attachment bytes could be copied into the rebuilt message.
        draft = draft_resource(draft_id="d")
        draft["message"]["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": header_list({"To": "b@y", "Subject": "hi"}),
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "a.pdf",
                    "body": {"attachmentId": "att-1", "size": 3},
                }
            ],
        }
        mock = make_mock_service()
        mock.users().drafts().get().execute.return_value = draft
        mock.users().messages().attachments().get().execute.configure_mock(
            side_effect=_http_error(404)
        )
        with _gmail(mock), pytest.raises(DraftGoneError):
            gmail_update_draft(
                GmailUpdateDraftInput(user_id="alice", draft_id="d", body="x")
            )

    def test_other_errors_pass_through(self):
        mock = make_mock_service()
        mock.users().drafts().send().execute.configure_mock(
            side_effect=_http_error(500)
        )
        with _gmail(mock), pytest.raises(HttpError):
            gmail_send(GmailSendInput(user_id="alice", draft_id="d"))


class TestRefusalsOverHTTP(TestTemplate):
    @pytest.mark.parametrize(
        ("error", "status"),
        [(DraftGoneError("d"), 404), (DuplicateReplyError("dup"), 409)],
    )
    def test_answer_with_their_own_status(self, error: ClientRefusalError, status):
        app = FastAPI()
        app.add_exception_handler(ClientRefusalError, client_refusal_handler)

        def _route():
            raise error

        app.add_api_route("/x", _route, methods=["POST"])
        resp = TestClient(app).post("/x")
        assert resp.status_code == status
        assert str(error) in resp.text


def _msg(mid: str, labels: list[str], sender: str, to: str, ago: timedelta) -> dict:
    # ``ago`` is stamped into internalDate when the call runs (see _stamped):
    # the guard compares against the clock then, not at collection.
    return {
        "id": mid,
        "labelIds": labels,
        "_ago": ago,
        "payload": {
            "headers": header_list({"From": sender, "To": to, "Subject": "Plans"})
        },
    }


def _theirs(mid: str, ago: timedelta, sender: str = "Bob <bob@x.com>") -> dict:
    return _msg(mid, ["INBOX"], sender, "alice@x.com", ago)


def _mine(mid: str, ago: timedelta, to: str = "bob@x.com") -> dict:
    return _msg(mid, ["SENT"], "alice@x.com", to, ago)


_HOUR, _MIN = timedelta(hours=1), timedelta(minutes=1)


def _stamped(messages: list[dict]) -> list[dict]:
    now = datetime.now(UTC)
    return [
        {k: v for k, v in m.items() if k != "_ago"}
        | {"internalDate": str(int((now - m["_ago"]).timestamp() * 1000))}
        for m in messages
    ]


def _reply(messages: list[dict], **kwargs) -> MagicMock:
    mock = make_mock_service()
    mock.users().threads().get().execute.return_value = {
        "id": "t",
        "messages": _stamped(messages),
    }
    mock.users().drafts().create().execute.return_value = {"id": "d-new"}
    mock.users().drafts().get().execute.return_value = draft_resource(
        draft_id="d-new", thread_id="t"
    )
    with _gmail(mock):
        gmail_reply_to_thread(
            GmailReplyInput(user_id="alice", thread_id="t", to="bob@x.com", **kwargs)
        )
    return mock


def _created_reply(mock: MagicMock) -> bool:
    # The last create() call is the service's; setup's bare create() has no body.
    body = mock.users().drafts().create.call_args.kwargs.get("body") or {}
    return (body.get("message") or {}).get("threadId") == "t"


class TestDuplicateReplyGuard(TestTemplate):
    def test_refuses_a_reply_the_user_just_sent(self):
        thread = [_theirs("m1", _HOUR), _mine("m2", 2 * _MIN)]
        with pytest.raises(DuplicateReplyError) as caught:
            _reply(thread)
        text = str(caught.value)
        assert "2 min ago" in text
        assert "to bob@x.com" in text
        assert "follow_up=true" in text

    def test_gmail_order_is_not_trusted(self):
        thread = [_mine("m2", 2 * _MIN), _theirs("m1", _HOUR)]
        with pytest.raises(DuplicateReplyError):
            _reply(thread)

    def test_follow_up_overrides(self):
        thread = [_theirs("m1", _HOUR), _mine("m2", 2 * _MIN)]
        assert _created_reply(_reply(thread, follow_up=True))

    @pytest.mark.parametrize(
        "thread",
        [
            [_theirs("m1", _HOUR), _mine("m2", 2 * _MIN), _theirs("m3", _MIN)],
            [_theirs("m1", _HOUR), _msg("m2", ["DRAFT"], "a@x", "bob@x.com", _MIN)],
            [_mine("m1", _HOUR), _mine("m2", _MIN)],
            [_theirs("m1", _HOUR), _msg("m2", ["SENT", "INBOX"], "a", "a", _MIN)],
            [_theirs("m1", 2 * _HOUR), _mine("m2", _HOUR)],
            [_theirs("m1", _HOUR), _mine("m2", _MIN, to="carol@x.com")],
        ],
        ids=[
            "they_replied_since",
            "only_a_draft",
            "own_thread",
            "note_to_self",
            "replied_long_ago",
            "forwarded_to_someone_else",
        ],
    )
    def test_drafts_when_the_reply_is_not_a_duplicate(self, thread):
        assert _created_reply(_reply(thread))

    def test_reply_to_header_counts_as_the_sender(self):
        incoming = _theirs("m1", _HOUR, sender="noreply@x.com")
        incoming["payload"]["headers"] += header_list({"Reply-To": "bob@x.com"})
        with pytest.raises(DuplicateReplyError):
            _reply([incoming, _mine("m2", _MIN)])


class TestComposerSendStatus(TestTemplate):
    """An unconfirmed send asks whether the draft is gone before saying it failed."""

    def test_gone_draft_reads_as_sent(self):
        from mcp_server.app_tools.gmail_composer import send_status  # noqa: PLC0415

        with _gmail(_gone_mailbox()):
            assert send_status(draft_id="d", user_id="alice").draft_exists is False

    def test_live_draft_reads_as_not_sent(self):
        from mcp_server.app_tools.gmail_composer import send_status  # noqa: PLC0415

        mock = make_mock_service()
        mock.users().drafts().get().execute.return_value = draft_resource()
        with _gmail(mock):
            assert send_status(draft_id="d", user_id="alice").draft_exists is True

    def test_other_gmail_errors_propagate(self):
        from mcp_server.app_tools.gmail_composer import send_status  # noqa: PLC0415

        mock = make_mock_service()
        mock.users().drafts().get().execute.configure_mock(side_effect=_http_error(500))
        with _gmail(mock), pytest.raises(HttpError):
            send_status(draft_id="d", user_id="alice")
