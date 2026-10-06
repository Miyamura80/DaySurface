"""Where a thread stands for triage: is it in the open inbox, is its verdict fresh?

Both questions turn on the same idea, an *incoming* message: one someone else
sent (or the user sent to themselves). The user's own replies and drafts are
not incoming, so they never reopen a resolved thread or invalidate a verdict.

- Open inbox: Gmail matches ``in:inbox -label:"MCP/Done"`` one message at a
  time, so a thread qualifies when some message is in INBOX without MCP/Done.
  A thread marked done therefore comes back exactly when someone writes to it.
- Freshness: a verdict goes stale only when an incoming message arrived after
  the row's watermark (the newest message the verdict accounts for). Gmail's
  per-thread ``historyId`` moves on any change (read state, labels, drafts),
  so it only serves as the free "nothing changed" fast path.

Gmail quota shapes how freshness is checked (see ``services._gmail_quota``):
one ``threads.get`` per moved thread can spend the user's whole minute. So a
``users.history.list`` pass (2 units a page) first rules out threads with no
message added since curation, and every request draws on one per-call
``QuotaBudget``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from loguru import logger as log

from models.curation import CurationState, LedgerStatus
from services._gmail_history import (
    HistoryGoneError,
    HistoryTooLargeError,
    iter_history_pages,
)
from services._gmail_quota import THREADS_GET_UNITS, QuotaBudget
from services.curation_ledger import LedgerRowStatus, load_status_map
from services.gmail_curate_svc import (
    _batch_get_threads,
    _build_label_lookups,
    _thread_has_noise_labels,
)
from services.gmail_message_roles import (
    EXCLUDED_CATEGORY_IDS,
    INBOX_LABEL_ID,
    is_incoming,
    may_be_incoming,
)
from services.gmail_messages_svc import _find_mcp_done_label, _internal_date_to_dt

# Pages one history probe may read (500 records each). A delta past that is
# too big to rule anything out cheaply, so the search moves to a newer start.
HISTORY_PROBE_PAGES = 5


def newest_incoming_at(messages: list[dict[str, Any]]) -> datetime | None:
    """Arrival time of the thread's newest incoming message."""
    times = [
        at
        for msg in messages
        if is_incoming(msg)
        and (at := _internal_date_to_dt(msg.get("internalDate"))) is not None
    ]
    return max(times, default=None)


def is_triageable(
    messages: list[dict[str, Any]],
    *,
    done_label_id: str | None,
    label_id_to_name: dict[str, str],
) -> bool:
    """Whether a thread belongs in the triageable inbox.

    Mirrors ``build_curate_query()`` for results Gmail did not filter (the
    incremental history delta, or a direct fetch). INBOX, MCP/Done and the
    excluded category tabs follow Gmail's per-message matching (see module
    docstring). Noise labels exclude the whole thread, as
    ``gmail_curate_inbox`` does.
    """
    in_open_inbox = any(
        INBOX_LABEL_ID in (labels := set(msg.get("labelIds") or []))
        and (done_label_id is None or done_label_id not in labels)
        and not labels & EXCLUDED_CATEGORY_IDS
        for msg in messages
    )
    return in_open_inbox and not _thread_has_noise_labels(messages, label_id_to_name)


def _history_moved(row: LedgerRowStatus, current_history_id: str | None) -> bool:
    # The fast path needs both watermarks; if either is unknown, check messages.
    if current_history_id is None or row.curated_history_id is None:
        return True
    return row.curated_history_id != current_history_id


def ledger_status_for(
    row: LedgerRowStatus | None,
    current_history_id: str | None,
    newest_incoming: datetime | None,
    *,
    check_freshness: bool = True,
) -> LedgerStatus:
    """Freshness of one ledger row for a thread in the triageable inbox.

    ``newest_incoming`` is only consulted when the historyId moved; pass
    ``None`` when it was not fetched, which then reads as stale rather than
    risk hiding a new reply.
    """
    if row is None or row.state == CurationState.pending:
        return LedgerStatus.uncurated
    if not check_freshness or not _history_moved(row, current_history_id):
        return LedgerStatus.curated
    if newest_incoming is None or row.watermark is None:
        return LedgerStatus.stale
    if newest_incoming > row.watermark:
        return LedgerStatus.stale
    return LedgerStatus.curated


def _as_int(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except ValueError:
        return None


def _added_since(svc: Any, start: int, budget: QuotaBudget) -> dict[str, int]:
    """Per thread, the newest history id that added a possibly incoming message.

    Covers everything after ``start``. Raises ``HistoryGoneError`` or
    ``HistoryTooLargeError`` when the history can't show a thread got nothing new.
    """
    newest: dict[str, int] = {}
    for page in iter_history_pages(
        svc,
        str(start),
        history_types=["messageAdded"],
        max_pages=HISTORY_PROBE_PAGES,
        budget=budget,
    ):
        for record in page.get("history") or []:
            # A record without an id can't be placed, so it is newer than
            # any verdict.
            at = _as_int(record.get("id")) or sys.maxsize
            for added in record.get("messagesAdded") or []:
                msg = added.get("message") or {}
                tid = msg.get("threadId")
                if tid and may_be_incoming(msg):
                    newest[tid] = max(newest.get(tid, at), at)
    return newest


def _oldest_usable_history(
    svc: Any, starts: list[int], budget: QuotaBudget
) -> tuple[int, dict[str, int]] | None:
    """History from the oldest of ``starts`` (ascending) it can be read from.

    A start is unusable when Gmail no longer keeps it (about a week back) or
    its delta outruns ``HISTORY_PROBE_PAGES``. Both get less likely as the
    start gets newer, so after the oldest fails a binary search finds the
    oldest that works. Each probe has its own page cap, so one oversized
    delta can't starve the newer probes.
    """

    def probe(i: int) -> tuple[int, dict[str, int]] | None:
        try:
            return starts[i], _added_since(svc, starts[i], budget)
        except (HistoryGoneError, HistoryTooLargeError) as exc:
            log.debug("History from {} unusable: {}", starts[i], type(exc).__name__)
            return None

    # The oldest start first: it is the common case and covers every row.
    best = probe(0)
    if best is not None:
        return best
    lo, hi = 1, len(starts) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        found = probe(mid)
        if found is None:
            lo = mid + 1
        else:
            best, hi = found, mid - 1
    return best


def _nothing_new(
    svc: Any,
    thread_ids: list[str],
    status_map: dict[str, LedgerRowStatus],
    budget: QuotaBudget,
) -> set[str]:
    """The ``thread_ids`` with no possibly incoming message since curation.

    A row's ``curated_history_id`` covers every message its verdict accounts
    for (``inbox_save_curation`` keeps the older id when mail lands mid-save),
    so a thread with no message added after it is fresh. Rows the history
    can't speak for (no stored id, or older than the history read) are not.
    """
    start_of = {tid: _as_int(status_map[tid].curated_history_id) for tid in thread_ids}
    starts = sorted({s for s in start_of.values() if s is not None})
    usable = _oldest_usable_history(svc, starts, budget) if starts else None
    if usable is None:
        return set()
    covered_from, added = usable
    return {
        tid
        for tid, start in start_of.items()
        if start is not None and start >= covered_from and added.get(tid, 0) <= start
    }


def resolve_statuses(
    svc: Any,
    status_map: dict[str, LedgerRowStatus],
    current_hist: dict[str, str | None],
    *,
    check_freshness: bool,
    budget: QuotaBudget,
) -> dict[str, LedgerStatus]:
    """Ledger status for every thread in ``current_hist`` (id -> historyId).

    Only threads whose historyId moved since curation are checked. The
    history delta clears those with no new message; the rest are fetched in
    batched ``format=minimal`` calls (labels + internalDate, no headers) in
    ``current_hist`` order while the budget lasts. Any left unfetched read as
    stale.
    """
    moved = (
        [
            tid
            for tid, hist in current_hist.items()
            if (row := status_map.get(tid)) is not None
            and row.state != CurationState.pending
            and _history_moved(row, hist)
        ]
        if check_freshness
        else []
    )
    cleared = _nothing_new(svc, moved, status_map, budget) if moved else set()
    # A thread with nothing new reads as if its historyId had not moved.
    effective_hist = current_hist | {
        tid: status_map[tid].curated_history_id for tid in cleared
    }
    to_fetch = budget.take([t for t in moved if t not in cleared], THREADS_GET_UNITS)
    fetched = _batch_get_threads(svc, to_fetch, fmt="minimal") if to_fetch else {}
    newest = {
        tid: newest_incoming_at(thread.get("messages") or [])
        for tid, thread in fetched.items()
    }
    return {
        tid: ledger_status_for(
            status_map.get(tid),
            hist,
            newest.get(tid),
            check_freshness=check_freshness,
        )
        for tid, hist in effective_hist.items()
    }


class BeyondScan:
    """Status of ledger threads outside the capped inbox scan, fetched on demand.

    Label lookups run once per instance (one ``inbox_get_curation`` call), not
    once per batch.
    """

    def __init__(
        self, svc: Any, user_id: str, *, check_freshness: bool, budget: QuotaBudget
    ) -> None:
        self._svc = svc
        self._user_id = user_id
        self._check_freshness = check_freshness
        self._budget = budget
        self._labels: tuple[dict[str, str], str | None] | None = None

    def statuses(self, thread_ids: list[str]) -> dict[str, LedgerStatus]:
        """Status per still-triageable thread; threads that left are absent.

        So are threads the budget can't pay to check. Past a full inbox scan
        they are most likely archived, and surfacing them would fill the read
        with old threads; ``include_inactive`` still returns them, as stale.
        """
        to_fetch = self._budget.take(thread_ids, THREADS_GET_UNITS)
        if not to_fetch:
            return {}
        if self._labels is None:
            label_id_to_name, _ = _build_label_lookups(self._svc)
            self._labels = (label_id_to_name, _find_mcp_done_label(self._svc))
        label_id_to_name, done_label_id = self._labels
        fetched = _batch_get_threads(self._svc, to_fetch, fmt="minimal")
        rows = load_status_map(self._user_id, to_fetch)
        out: dict[str, LedgerStatus] = {}
        for tid in to_fetch:
            thread = fetched.get(tid)
            if thread is None:
                # The fetch failed, so membership is unknown: surface the row
                # for another look rather than hide it.
                out[tid] = LedgerStatus.stale
                continue
            messages = thread.get("messages") or []
            if not is_triageable(
                messages,
                done_label_id=done_label_id,
                label_id_to_name=label_id_to_name,
            ):
                continue
            hist = thread.get("historyId")
            out[tid] = ledger_status_for(
                rows.get(tid),
                None if hist is None else str(hist),
                newest_incoming_at(messages),
                check_freshness=self._check_freshness,
            )
        return out
