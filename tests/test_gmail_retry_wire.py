"""Gmail rate-limit backoff end to end: MCP wire -> service -> real HTTP socket.

``test_gmail_retry`` covers the request class over ``HttpMockSequence``. This
goes further: a real discovery-built Gmail client talks over httplib2 to a
local stand-in for gmail.googleapis.com that answers with the quota 403 Gmail
sent in production, and ``inbox_get_curation`` is called as an MCP client
would call it. It proves the retry survives the real transport (status and
header parsing included) and that the host sees the clean error text.
"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httplib2
from cryptography.fernet import Fernet
from googleapiclient.discovery import build

from common import token_encryption
from common.token_encryption import FernetEncryption
from models.curation import CurationBucket, ThreadJudgment
from services import _gmail_retry
from services._gmail_retry import RateLimitRetryingRequest
from services.curation_ledger import upsert_judgments
from tests.test_mcp_e2e import _wire_session
from tests.test_template import TestTemplate

_QUOTA_BODY = json.dumps(
    {
        "error": {
            "code": 403,
            "message": "Quota exceeded for quota metric 'Total Query Cost' and "
            "limit 'Units per minute per user' of service 'gmail.googleapis.com' "
            "for consumer 'project_number:619421638255'.",
            "errors": [
                {
                    "message": "Quota exceeded",
                    "domain": "usageLimits",
                    "reason": "rateLimitExceeded",
                }
            ],
            "status": "PERMISSION_DENIED",
        }
    }
).encode()


_THREADS_OK = json.dumps({"threads": [{"id": "t1", "historyId": "100"}]}).encode()


def _curation_responder(rate_limited_requests: int):
    """``threads.list``: the first N calls get the quota 403, then 200."""

    def respond(_method: str, _path: str, hits: list[str]) -> tuple[int, bytes]:
        if len(hits) <= rate_limited_requests:
            return 403, _QUOTA_BODY
        return 200, _THREADS_OK

    return respond


@contextmanager
def _fake_gmail(respond):
    """Local stand-in for gmail.googleapis.com; ``respond`` scripts each reply."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            hits.append(f"{self.command} {self.path.split('?')[0]}")
            status, body = respond(self.command, self.path, hits)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._reply()

        def do_POST(self) -> None:
            self._reply()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = build(
            "gmail",
            "v1",
            http=httplib2.Http(proxy_info=None),
            requestBuilder=RateLimitRetryingRequest,
            static_discovery=True,
            client_options={"api_endpoint": f"http://127.0.0.1:{server.server_port}"},
        )
        yield client, hits
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def _curation_session(user: str, client):
    enc = FernetEncryption(Fernet.generate_key().decode())
    with (
        patch.object(token_encryption, "require_encryption", return_value=enc),
        _wire_session(user) as session,
        patch("services.inbox_curation_svc._get_gmail_client", return_value=client),
    ):
        upsert_judgments(
            user,
            [
                ThreadJudgment(
                    thread_id="t1",
                    bucket=CurationBucket.needs_reply,
                    summary="deck due Friday",
                )
            ],
            history_ids={"t1": "100"},
        )
        yield session


def _call_curation(session) -> dict:
    return session.request(
        "tools/call", {"name": "inbox_get_curation", "arguments": {}}
    )


class TestGmailRateLimitOverTheWire(TestTemplate):
    def test_quota_403s_clear_and_the_tool_succeeds(self):
        with (
            patch.object(_gmail_retry.time, "sleep") as sleep,
            _fake_gmail(_curation_responder(2)) as (client, hits),
            _curation_session("u-retry-ok", client) as session,
        ):
            result = _call_curation(session)

        assert result["isError"] is False
        assert result["structuredContent"]["coverage"]["curated"] == 1
        assert result["structuredContent"]["records"][0]["thread_id"] == "t1"
        # Two rejected, the third answered: the retry rode the real socket.
        assert len(hits) == 3
        assert all("/gmail/v1/users/me/threads" in path for path in hits)
        assert sleep.call_count == 2

    def test_persistent_quota_gives_the_host_clean_recovery_text(self):
        with (
            patch.object(_gmail_retry.time, "sleep"),
            _fake_gmail(_curation_responder(100)) as (client, hits),
            _curation_session("u-retry-exhausted", client) as session,
        ):
            result = _call_curation(session)

        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "rate limit" in text
        assert "retry" in text
        # The production failure leaked these verbatim.
        assert "project_number" not in text
        assert "HttpError" not in text
        assert len(hits) == _gmail_retry._MAX_RETRIES + 1

    def test_persistent_quota_is_a_429_on_the_rest_api(self):
        # Same service on the HTTP transport: a back-off status with
        # Retry-After, not the generic 500 an unmapped exception becomes.
        with (
            patch.object(_gmail_retry.time, "sleep"),
            _fake_gmail(_curation_responder(100)) as (client, _hits),
            _curation_session("u-retry-rest", client) as session,
        ):
            resp = session._client.post(
                "/api/v1/services/inbox_get_curation",
                json={},
                headers={"X-API-KEY": session._api_key},
            )

        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "60"
        error = resp.json()["error"]
        assert error["code"] == "rate_limited"
        assert "project_number" not in error["message"]


_DRAFT_CREATED = json.dumps(
    {"id": "d1", "message": {"id": "m1", "threadId": "th1"}}
).encode()
_DRAFT_FULL = json.dumps(
    {
        "id": "d1",
        "message": {
            "id": "m1",
            "threadId": "th1",
            "labelIds": ["DRAFT"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "To", "value": "a@example.com"},
                    {"name": "Subject", "value": "hi"},
                ],
                "body": {"data": ""},
            },
        },
    }
).encode()


def _compose_responder(quota: dict[str, bool]):
    """``drafts.create`` + its re-read, each refused while its flag is set."""

    def respond(method: str, _path: str, _hits: list[str]) -> tuple[int, bytes]:
        if method == "POST":
            return (403, _QUOTA_BODY) if quota["create"] else (200, _DRAFT_CREATED)
        return (403, _QUOTA_BODY) if quota["read"] else (200, _DRAFT_FULL)

    return respond


def _compose(session, key: str):
    return session._client.post(
        "/api/v1/services/gmail_compose",
        json={"to": "a@example.com", "subject": "hi", "body": "hello"},
        headers={"X-API-KEY": session._api_key, "Idempotency-Key": key},
    )


class TestRateLimitIdempotency(TestTemplate):
    """A 429 on a mutating REST route must agree with its Idempotency-Key."""

    def test_refused_before_any_write_frees_the_key_for_a_retry(self):
        quota = {"create": True, "read": True}
        with (
            patch.object(_gmail_retry.time, "sleep"),
            _fake_gmail(_compose_responder(quota)) as (client, hits),
            _wire_session("u-idem-free") as session,
            patch("services.gmail_drafts_svc._get_gmail_client", return_value=client),
        ):
            assert _compose(session, "k-free").status_code == 429
            quota.update(create=False, read=False)
            # Following Retry-After with the same key works: nothing was written.
            retry = _compose(session, "k-free")

        assert retry.status_code == 200, retry.text
        assert retry.json()["draft_id"] == "d1"
        writes = [h for h in hits if h.startswith("POST")]
        # Five refused creates, then the one that landed.
        assert len(writes) == _gmail_retry._MAX_RETRIES + 2

    def test_refused_after_a_write_keeps_the_key_claimed(self):
        # The draft was created and only its re-read was refused: a same-key
        # retry must not create a second draft.
        quota = {"create": False, "read": True}
        with (
            patch.object(_gmail_retry.time, "sleep"),
            _fake_gmail(_compose_responder(quota)) as (client, hits),
            _wire_session("u-idem-held") as session,
            patch("services.gmail_drafts_svc._get_gmail_client", return_value=client),
        ):
            assert _compose(session, "k-held").status_code == 429
            quota["read"] = False
            retry = _compose(session, "k-held")

        assert retry.status_code == 409
        assert [h for h in hits if h.startswith("POST")] == [
            "POST /gmail/v1/users/me/drafts"
        ]
