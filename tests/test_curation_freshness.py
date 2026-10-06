"""Freshness checks in inbox_get_curation that keep it inside Gmail's quota.

The history prefilter, the oldest-usable-start search, and the per-call
quota budget (``services.curation_status``, ``services._gmail_quota``). The
read itself runs through ``CurationReadHarness`` with Gmail patched out.
"""

from __future__ import annotations

from models.curation import GetCurationInput, LedgerStatus
from services._gmail_quota import (
    DEFAULT_CALL_UNITS,
    HISTORY_LIST_UNITS,
    THREADS_GET_UNITS,
)
from services.curation_ledger import upsert_judgments
from services.curation_status import HISTORY_PROBE_PAGES
from tests.test_inbox_curation import (
    _EARLIER,
    _LATER,
    CurationReadHarness,
    _added,
    _full_scan,
    _judgment,
    _msg_at,
    _patch_db,
    _patch_fernet,
    _stub,
)
from tests.test_template import TestTemplate

# history.list keeps answering "there's more" from this start.
_ENDLESS = {"history": [], "nextPageToken": "more"}


class TestHistoryPrefilter(CurationReadHarness, TestTemplate):
    def test_read_or_label_churn_needs_no_thread_fetch(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            # historyId moved but no message was added since curation.
            res = self._run_get([_stub("t1", "150")], history={"100": []})
            assert res.records[0].ledger_status == LedgerStatus.curated
            assert self.fetched_ids == []
            assert self.history_starts == ["100"]

    def test_new_incoming_message_is_fetched_and_judged(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            res = self._run_get(
                [_stub("t1", "150")],
                threads={"t1": [_msg_at(_LATER, "INBOX")]},
                history={"100": [_added("120", "t1", "INBOX", "UNREAD")]},
            )
            assert res.records[0].ledger_status == LedgerStatus.stale
            assert self.fetched_ids == [["t1"]]

    def test_own_reply_or_draft_needs_no_thread_fetch(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            res = self._run_get(
                [_stub("t1", "150")],
                history={
                    "100": [_added("110", "t1", "DRAFT"), _added("120", "t1", "SENT")]
                },
            )
            assert res.records[0].ledger_status == LedgerStatus.curated
            assert self.fetched_ids == []

    def test_message_added_before_a_rows_curation_is_ignored(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments(
                "alice",
                [_judgment("t1"), _judgment("t2")],
                history_ids={"t1": "100", "t2": "50"},
            )
            # One pass from the older start: t1's message (id 80) predates
            # t1's own verdict (100), so it does not make t1 a candidate.
            res = self._run_get(
                [_stub("t1", "150"), _stub("t2", "150")],
                history={"50": [_added("80", "t1", "INBOX")]},
            )
            assert {r.ledger_status for r in res.records} == {LedgerStatus.curated}
            assert self.fetched_ids == []
            assert self.history_starts == ["50"]

    def test_record_without_an_id_counts_as_new_for_every_row(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments(
                "alice",
                [_judgment("t1"), _judgment("t2")],
                history_ids={"t1": "100", "t2": "50"},
            )
            unplaced = _added("0", "t1", "INBOX")
            del unplaced["id"]
            # The pass starts at t2's 50, yet a record it can't place must
            # still count as newer than t1's own verdict at 100.
            self._run_get(
                [_stub("t1", "150"), _stub("t2", "150")],
                threads={"t1": [_msg_at(_LATER, "INBOX")]},
                history={"50": [unplaced]},
            )
            assert self.fetched_ids == [["t1"]]


class TestOldestUsableStart(CurationReadHarness, TestTemplate):
    def _three_rows(self, history):
        upsert_judgments(
            "alice",
            [_judgment("old"), _judgment("mid"), _judgment("new")],
            history_ids={"old": "10", "mid": "60", "new": "100"},
        )
        return self._run_get(
            [_stub("old", "150"), _stub("mid", "150"), _stub("new", "150")],
            threads={"old": [_msg_at(_EARLIER, "INBOX")]},
            history=history,
        )

    def test_expired_history_settles_on_the_oldest_start_gmail_keeps(self):
        with _patch_db(), _patch_fernet():
            res = self._three_rows({"60": [], "100": []})
            assert {r.ledger_status for r in res.records} == {LedgerStatus.curated}
            # Gmail refused 10, the search settled on 60: only the row older
            # than any history Gmail still keeps needed a thread fetch.
            assert self.history_starts == ["10", "60"]
            assert self.fetched_ids == [["old"]]

    def test_an_oversized_delta_does_not_starve_newer_starts(self):
        with _patch_db(), _patch_fernet():
            res = self._three_rows({"10": _ENDLESS, "60": [], "100": []})
            assert {r.ledger_status for r in res.records} == {LedgerStatus.curated}
            # The oldest start ran out its own page cap; the next probe still
            # had a full allowance and cleared the newer rows.
            assert self.history_starts == ["10"] * HISTORY_PROBE_PAGES + ["60"]
            assert self.fetched_ids == [["old"]]

    def test_no_usable_start_falls_back_to_fetching(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            res = self._run_get(
                [_stub("t1", "150")],
                threads={"t1": [_msg_at(_EARLIER, "INBOX")]},
                history={"100": _ENDLESS},
            )
            assert res.records[0].ledger_status == LedgerStatus.curated
            assert len(self.history_starts) == HISTORY_PROBE_PAGES
            assert self.fetched_ids == [["t1"]]


class TestQuotaBudget(CurationReadHarness, TestTemplate):
    def test_freshness_fetches_stop_at_the_budget(self):
        with _patch_db(), _patch_fernet():
            ids = [f"t{i}" for i in range(60)]
            upsert_judgments(
                "alice",
                [_judgment(t) for t in ids],
                history_ids=dict.fromkeys(ids, "100"),
            )
            # History is gone (one refused probe), so every moved row is a
            # fetch candidate.
            res = self._run_get(
                [_stub(t, "150") for t in ids],
                GetCurationInput(user_id="alice", limit=100),
                threads={t: [_msg_at(_EARLIER, "INBOX")] for t in ids},
            )
            allowed = (DEFAULT_CALL_UNITS - HISTORY_LIST_UNITS) // THREADS_GET_UNITS
            # The newest threads were checked; the rest read as stale.
            assert self.fetched_ids == [ids[:allowed]]
            assert res.coverage.curated == allowed
            assert res.coverage.stale == len(ids) - allowed

    def _beyond_scan(self, inp):
        old = [f"a{i}" for i in range(60)]
        upsert_judgments(
            "alice",
            [_judgment(t, importance=0.9 - i / 1000) for i, t in enumerate(old)],
            history_ids={},
        )
        # All still open, but outside the full scan.
        res = self._run_get(
            _full_scan(), inp, threads={t: [_msg_at(_EARLIER, "INBOX")] for t in old}
        )
        return old, res

    def test_beyond_scan_rows_past_the_budget_are_hidden(self):
        with _patch_db(), _patch_fernet():
            old, res = self._beyond_scan(GetCurationInput(user_id="alice", limit=100))
            allowed = DEFAULT_CALL_UNITS // THREADS_GET_UNITS
            assert sum(len(ids) for ids in self.fetched_ids) == allowed
            # Past a full scan an unchecked row is most likely archived, so it
            # is left out rather than filling the read as "stale".
            assert [r.thread_id for r in res.records] == old[:allowed]
            assert {r.ledger_status for r in res.records} == {LedgerStatus.curated}

    def test_include_inactive_still_returns_unchecked_rows_as_stale(self):
        with _patch_db(), _patch_fernet():
            old, res = self._beyond_scan(
                GetCurationInput(user_id="alice", limit=100, include_inactive=True)
            )
            allowed = DEFAULT_CALL_UNITS // THREADS_GET_UNITS
            unchecked = [r for r in res.records if r.thread_id in old[allowed:]]
            assert len(unchecked) == len(old) - allowed
            assert {r.ledger_status for r in unchecked} == {LedgerStatus.stale}
