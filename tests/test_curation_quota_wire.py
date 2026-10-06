"""Gmail quota one ``inbox_get_curation`` call spends, measured on the wire.

A real discovery-built client talks over httplib2 to a local stand-in for
gmail.googleapis.com (batch endpoint included), and the stand-in totals the
quota units each request would cost at the new-tier prices Google introduced
in May 2026 (6,000 units per user per minute, ``threads.get`` 40 units). The
morning case that failed in production: an inbox where every curated
thread's historyId moved overnight (read, labelled), and only a few got mail.
"""

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from models.curation import CurationBucket, ThreadJudgment
from services._gmail_quota import DEFAULT_CALL_UNITS
from services.curation_ledger import upsert_judgments
from tests.test_gmail_retry_wire import _call_curation, _curation_session, _fake_gmail
from tests.test_template import TestTemplate

_UNITS = {"threads.list": 10, "threads.get": 40, "history.list": 2}
_PER_USER_MINUTE = 6_000
_LATER_MS = str(int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000))
_EARLIER_MS = str(int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000))
_PART = re.compile(rb"Content-ID: <([^>]+)>\r?\n\r?\nGET (\S+)")
_NOT_FOUND = json.dumps({"error": {"code": 404, "message": "Not Found"}}).encode()


class _Mailbox:
    """Serves an inbox and tallies the quota units every request costs.

    ``history`` maps a startHistoryId to the thread ids that got a new
    incoming message after it; any other start is gone (404).
    """

    def __init__(self, size: int, new_mail: set[str], history: dict[str, list[str]]):
        self.inbox = [f"t{i}" for i in range(size)]
        self.new_mail = new_mail
        self.history = history
        self.units: dict[str, int] = {}
        self.threads_fetched: list[str] = []
        self.history_starts: list[str] = []

    def _charge(self, method: str, count: int = 1) -> None:
        self.units[method] = self.units.get(method, 0) + _UNITS[method] * count

    def _thread(self, tid: str) -> dict:
        at = _LATER_MS if tid in self.new_mail else _EARLIER_MS
        messages = [{"id": f"{tid}-m", "labelIds": ["INBOX"], "internalDate": at}]
        return {"id": tid, "historyId": "500", "messages": messages}

    def _batch(self, body: bytes) -> tuple[int, bytes, str]:
        parts = []
        for content_id, path in _PART.findall(body):
            tid = urlparse(path.decode()).path.rsplit("/", 1)[-1]
            self.threads_fetched.append(tid)
            parts.append(
                f"--B\r\nContent-Type: application/http\r\n"
                f"Content-ID: <response-{content_id.decode()}>\r\n\r\n"
                f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                f"{json.dumps(self._thread(tid))}\r\n"
            )
        self._charge("threads.get", len(parts))
        reply = "".join(parts) + "--B--\r\n"
        return 200, reply.encode(), "multipart/mixed; boundary=B"

    def respond(self, method: str, path: str, _hits: list[str], body: bytes):
        url = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if method == "POST" and url.path == "/batch":
            return self._batch(body)
        if url.path.endswith("/threads"):
            self._charge("threads.list")
            start = int(query.get("pageToken", 0))
            end = start + int(query["maxResults"])
            stubs = [{"id": t, "historyId": "500"} for t in self.inbox[start:end]]
            page: dict = {"threads": stubs}
            if end < len(self.inbox):
                page["nextPageToken"] = str(end)
            return 200, json.dumps(page).encode()
        if url.path.endswith("/history"):
            self._charge("history.list")
            start = query["startHistoryId"]
            self.history_starts.append(start)
            if start not in self.history:
                return 404, _NOT_FOUND
            records = [
                {
                    "id": "300",
                    "messagesAdded": [
                        {"message": {"threadId": t, "labelIds": ["INBOX"]}}
                    ],
                }
                for t in self.history[start]
            ]
            return 200, json.dumps({"history": records}).encode()
        return 500, json.dumps({"error": f"unexpected {method} {path}"}).encode()


@contextmanager
def _morning(user: str, mailbox: _Mailbox, curated: dict[str, str | None]):
    """``curated`` maps each curated thread to the historyId it was saved at."""
    with (
        _fake_gmail(mailbox.respond) as (client, _hits),
        _curation_session(user, client) as session,
    ):
        upsert_judgments(
            user,
            [
                ThreadJudgment(thread_id=t, bucket=CurationBucket.fyi, summary=t)
                for t in curated
            ],
            history_ids=curated,
            seen_through=dict.fromkeys(curated, datetime(2021, 1, 1, tzinfo=UTC)),
        )
        yield session


class TestCurationQuotaOverTheWire(TestTemplate):
    def test_a_moved_morning_inbox_costs_a_sliver_of_the_minute(self):
        new_mail = {"t3", "t40", "t150"}
        mailbox = _Mailbox(1_500, new_mail, history={"100": sorted(new_mail)})
        curated: dict[str, str | None] = {f"t{i}": "100" for i in range(200)}
        with _morning("u-quota", mailbox, curated) as session:
            result = _call_curation(session)

        assert result["isError"] is False, result
        coverage = result["structuredContent"]["coverage"]
        assert coverage == {"curated": 197, "stale": 3, "uncurated": 0}
        # Only the threads with new mail were fetched; one history pass
        # cleared the other 197 instead of 197 more 40-unit reads.
        assert sorted(mailbox.threads_fetched) == sorted(new_mail)
        assert mailbox.history_starts == ["100"]
        # threads.list x3 (1,500 stubs), history.list x1, threads.get x3.
        assert sum(mailbox.units.values()) == 3 * 10 + 2 + 3 * 40
        # Before this fix: 200 x threads.get = 8,000+ units, past the minute.
        assert sum(mailbox.units.values()) < _PER_USER_MINUTE // 20

    def test_full_scan_with_expired_history_stays_within_the_budget(self):
        # 2,500 threads fill the scan. 50 rows were curated before the oldest
        # history Gmail keeps, so only a thread fetch can clear them.
        new_mail = {"t3", "t40", "t120"}
        mailbox = _Mailbox(2_500, new_mail, history={"100": sorted(new_mail)})
        curated: dict[str, str | None] = {f"t{i}": "100" for i in range(150)}
        curated |= {f"t{i}": "10" for i in range(150, 200)}
        with _morning("u-quota-full", mailbox, curated) as session:
            result = _call_curation(session)

        assert result["isError"] is False, result
        # Gmail refused 10; the search settled on 100.
        assert mailbox.history_starts == ["10", "100"]
        fetched = len(mailbox.threads_fetched)
        # The budget paid for two probes and as many fetches as fit after.
        assert fetched == (DEFAULT_CALL_UNITS - 2 * 2) // 40
        coverage = result["structuredContent"]["coverage"]
        unchecked = len(new_mail) + 50 - fetched
        assert coverage["stale"] == len(new_mail) + unchecked
        assert coverage["curated"] == 200 - coverage["stale"]
        spent = sum(mailbox.units.values()) - mailbox.units["threads.list"]
        assert spent <= DEFAULT_CALL_UNITS
        # The scan itself: four pages of 500 stubs.
        assert mailbox.units["threads.list"] == 4 * 10
