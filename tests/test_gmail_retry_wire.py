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


@contextmanager
def _fake_gmail(rate_limited_requests: int):
    """Serve ``threads.list``: the first N calls get the quota 403, then 200."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            if len(hits) <= rate_limited_requests:
                status, body = 403, _QUOTA_BODY
            else:
                status = 200
                body = json.dumps({"threads": [{"id": "t1", "historyId": "100"}]})
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
            _fake_gmail(rate_limited_requests=2) as (client, hits),
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
            _fake_gmail(rate_limited_requests=100) as (client, hits),
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
            _fake_gmail(rate_limited_requests=100) as (client, _hits),
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
