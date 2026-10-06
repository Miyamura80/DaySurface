"""Gmail quota one ``inbox_get_curation`` call spends, measured on the wire.

A real discovery-built client talks over httplib2 to a local stand-in for
gmail.googleapis.com (batch endpoint included), and the stand-in totals the
quota units each request would cost at the new-tier prices Google introduced
in May 2026 (6,000 units per user per minute, ``threads.get`` 40 units). The
morning case that failed in production: a large inbox where every curated
thread's historyId moved overnight (read, labelled), and only a few got mail.
"""

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from models.curation import CurationBucket, ThreadJudgment
from services.curation_ledger import upsert_judgments
from tests.test_gmail_retry_wire import _call_curation, _curation_session, _fake_gmail
from tests.test_template import TestTemplate

_UNITS = {"threads.list": 10, "threads.get": 40, "history.list": 2}
_PER_USER_MINUTE = 6_000

_INBOX = [f"t{i}" for i in range(1_500)]
_CURATED = _INBOX[:200]
_WITH_NEW_MAIL = {"t3", "t40", "t150"}
_CURATED_AT_HISTORY = "100"
_LATER_MS = str(int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000))
_PART_ID = re.compile(rb"Content-ID: <([^>]+)>\r?\n\r?\nGET (\S+)")


def _thread(tid: str) -> dict:
    messages = [{"id": f"{tid}-m", "labelIds": ["INBOX"], "internalDate": _LATER_MS}]
    return {"id": tid, "historyId": "500", "messages": messages}


def _batch_reply(body: bytes) -> tuple[int, bytes, str]:
    """Answer a Gmail batch request: one threads.get reply per part."""
    parts = []
    for content_id, path in _PART_ID.findall(body):
        tid = urlparse(path.decode()).path.rsplit("/", 1)[-1]
        payload = json.dumps(_thread(tid))
        parts.append(
            f"--BOUNDARY\r\nContent-Type: application/http\r\n"
            f"Content-ID: <response-{content_id.decode()}>\r\n\r\n"
            f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{payload}\r\n"
        )
    reply = "".join(parts) + "--BOUNDARY--\r\n"
    return 200, reply.encode(), "multipart/mixed; boundary=BOUNDARY"


class _Mailbox:
    """Serves the inbox and tallies the quota units every request costs."""

    def __init__(self) -> None:
        self.units: dict[str, int] = {}
        self.threads_fetched: list[str] = []

    def _charge(self, method: str, count: int = 1) -> None:
        self.units[method] = self.units.get(method, 0) + _UNITS[method] * count

    def respond(self, method: str, path: str, _hits: list[str], body: bytes):
        url = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if method == "POST" and url.path.startswith("/batch"):
            reply = _batch_reply(body)
            fetched = [
                urlparse(p.decode()).path.rsplit("/", 1)[-1]
                for _, p in _PART_ID.findall(body)
            ]
            self.threads_fetched.extend(fetched)
            self._charge("threads.get", len(fetched))
            return reply
        if url.path.endswith("/threads"):
            self._charge("threads.list")
            start = int(query.get("pageToken", 0))
            end = start + int(query["maxResults"])
            page: dict = {
                "threads": [{"id": t, "historyId": "500"} for t in _INBOX[start:end]]
            }
            if end < len(_INBOX):
                page["nextPageToken"] = str(end)
            return 200, json.dumps(page).encode()
        if url.path.endswith("/history"):
            self._charge("history.list")
            assert query["startHistoryId"] == _CURATED_AT_HISTORY
            records = [
                {
                    "id": str(200 + i),
                    "messagesAdded": [
                        {
                            "message": {
                                "id": f"{t}-m",
                                "threadId": t,
                                "labelIds": ["INBOX"],
                            }
                        }
                    ],
                }
                for i, t in enumerate(sorted(_WITH_NEW_MAIL))
            ]
            return 200, json.dumps({"history": records}).encode()
        raise AssertionError(f"unexpected Gmail request: {method} {path}")


@contextmanager
def _morning_inbox(user: str):
    mailbox = _Mailbox()
    with (
        _fake_gmail(mailbox.respond) as (client, _hits),
        _curation_session(user, client) as session,
    ):
        upsert_judgments(
            user,
            [
                ThreadJudgment(
                    thread_id=t, bucket=CurationBucket.fyi, summary=f"about {t}"
                )
                for t in _CURATED
            ],
            history_ids=dict.fromkeys(_CURATED, _CURATED_AT_HISTORY),
            seen_through=dict.fromkeys(_CURATED, datetime(2020, 1, 1, tzinfo=UTC)),
        )
        yield session, mailbox


class TestCurationQuotaOverTheWire(TestTemplate):
    def test_a_moved_morning_inbox_costs_a_sliver_of_the_minute(self):
        with _morning_inbox("u-quota") as (session, mailbox):
            result = _call_curation(session)

        assert result["isError"] is False, result
        coverage = result["structuredContent"]["coverage"]
        assert coverage["stale"] == len(_WITH_NEW_MAIL)
        assert coverage["curated"] == len(_CURATED) - len(_WITH_NEW_MAIL)
        # Only the threads with new mail were fetched; the rest were cleared by
        # one history pass instead of 197 more 40-unit reads.
        assert sorted(mailbox.threads_fetched) == sorted(_WITH_NEW_MAIL)
        total = sum(mailbox.units.values())
        # threads.list x3 (1,500 stubs), history.list x1, threads.get x3.
        assert total == 3 * 10 + 2 + 3 * 40
        # Before: 200 x threads.get = 8,000+ units, over the whole minute.
        assert total < _PER_USER_MINUTE // 20
