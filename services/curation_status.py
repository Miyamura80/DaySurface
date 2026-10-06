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

Gmail quota shapes how freshness is checked. A per-user budget of 6,000
units a minute (new Cloud projects since May 2026) against 40 units per
``threads.get`` means one fetch per moved thread can spend the whole minute
on a single read. So one ``users.history.list`` pass (2 units a page) first
rules out threads where no message was added since curation, and the
threads.get calls that remain draw on a per-call ``FetchBudget``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger as log

from models.curation import CurationState, LedgerStatus
from services.curation_ledger import LedgerRowStatus, load_status_map
from services.gmail_curate_svc import (
    _batch_get_threads,
    _build_label_lookups,
    _thread_has_noise_labels,
)
from services.gmail_messages_svc import _find_mcp_done_label, _internal_date_to_dt

# ``threads.get`` at the new-tier price; on older projects it is 10, so the
# budget below only errs toward spending less.
THREADS_GET_UNITS = 40
# Units one inbox_get_curation call may spend on thread fetches: a third of
# the strictest per-user minute, so a follow-up inbox_search still fits.
CURATION_FETCH_BUDGET_UNITS = 2_000
_HISTORY_PAGE_SIZE = 500
# history.list pages one call may read across every probe (2 units each).
# Past that the delta is too big to rule anything out cheaply.
_HISTORY_MAX_PAGES = 10

_INBOX_LABEL_ID = "INBOX"
_SENT_LABEL_ID = "SENT"
_DRAFT_LABEL_ID = "DRAFT"
# Category tabs ``build_curate_query()`` excludes with ``-category:...``.
_EXCLUDED_CATEGORY_IDS = frozenset(
    {"CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
)


def is_incoming(msg: dict[str, Any]) -> bool:
    """A message someone else sent, or one the user sent to themselves."""
    labels = set(msg.get("labelIds") or [])
    # Drafts aren't mail, and category-tab mail is outside triage (see
    # is_triageable), so neither can make a verdict stale.
    if _DRAFT_LABEL_ID in labels or labels & _EXCLUDED_CATEGORY_IDS:
        return False
    return _SENT_LABEL_ID not in labels or _INBOX_LABEL_ID in labels


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
        _INBOX_LABEL_ID in (labels := set(msg.get("labelIds") or []))
        and (done_label_id is None or done_label_id not in labels)
        and not labels & _EXCLUDED_CATEGORY_IDS
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


class FetchBudget:
    """Gmail quota units one tool call may still spend on thread fetches."""

    def __init__(self, units: int) -> None:
        self.remaining = units

    def take(self, thread_ids: list[str]) -> list[str]:
        """The leading ``thread_ids`` the budget covers; it is charged for them."""
        allowed = thread_ids[: max(self.remaining, 0) // THREADS_GET_UNITS]
        self.remaining -= len(allowed) * THREADS_GET_UNITS
        if len(allowed) < len(thread_ids):
            log.info(
                "Gmail fetch budget spent: {} of {} threads left unchecked",
                len(thread_ids) - len(allowed),
                len(thread_ids),
            )
        return allowed


def _as_int(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except ValueError:
        return None


def _may_be_incoming(msg: dict[str, Any]) -> bool:
    """``is_incoming`` on the labels a message was added with.

    Category tabs are not ruled out here: a message can be moved out of one
    after it arrives, so only drafts and the user's own sent mail are.
    """
    labels = set(msg.get("labelIds") or [])
    if _DRAFT_LABEL_ID in labels:
        return False
    return _SENT_LABEL_ID not in labels or _INBOX_LABEL_ID in labels


def _added_since(svc: Any, start: int, pages: list[int]) -> dict[str, int] | None:
    """Per thread, the newest history id that added a possibly incoming message.

    Covers everything after ``start``. ``None`` when Gmail no longer keeps
    history that old (404), or when the delta outruns the pages left in
    ``pages[0]`` (shared across probes): either way it can't show that a
    thread got nothing new.
    """
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    newest: dict[str, int] = {}
    page_token: str | None = None
    while pages[0] > 0:
        pages[0] -= 1
        try:
            resp = (
                svc.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=str(start),
                    historyTypes=["messageAdded"],
                    maxResults=_HISTORY_PAGE_SIZE,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise
        for record in resp.get("history") or []:
            # A record without an id can't be placed, so it counts as new.
            record_id = _as_int(record.get("id"))
            for added in record.get("messagesAdded") or []:
                msg = added.get("message") or {}
                tid = msg.get("threadId")
                if tid and _may_be_incoming(msg):
                    at = start + 1 if record_id is None else record_id
                    newest[tid] = max(newest.get(tid, at), at)
        page_token = resp.get("nextPageToken")
        if not page_token:
            return newest
    return None


def _oldest_usable_history(
    svc: Any, starts: list[int]
) -> tuple[int, dict[str, int]] | None:
    """History from the oldest of ``starts`` (ascending) that Gmail still serves.

    Gmail keeps about a week of history, so the oldest start is tried first
    and, if refused, a binary search finds the oldest one accepted (2 units a
    probe). All probes share one page allowance, which bounds the latency.
    """
    pages = [_HISTORY_MAX_PAGES]
    found = _added_since(svc, starts[0], pages)
    if found is not None:
        return starts[0], found
    best: tuple[int, dict[str, int]] | None = None
    lo, hi = 1, len(starts) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        found = _added_since(svc, starts[mid], pages)
        if found is None:
            lo = mid + 1
        else:
            best = (starts[mid], found)
            hi = mid - 1
    return best


def _may_have_new_mail(
    svc: Any, thread_ids: list[str], status_map: dict[str, LedgerRowStatus]
) -> list[str]:
    """The subset of ``thread_ids`` that may have mail newer than their verdict.

    A row's ``curated_history_id`` already covers every message its verdict
    accounts for (``inbox_save_curation`` keeps the older id when mail lands
    mid-save), so a thread with no message added after it is fresh. Rows the
    history can't speak for (no stored id, or older than Gmail keeps) stay in.
    """
    start_of = {tid: _as_int(status_map[tid].curated_history_id) for tid in thread_ids}
    starts = sorted({s for s in start_of.values() if s is not None})
    usable = _oldest_usable_history(svc, starts) if starts else None
    if usable is None:
        return thread_ids
    covered_from, added = usable
    return [
        tid
        for tid in thread_ids
        if (start := start_of[tid]) is None
        or start < covered_from
        or added.get(tid, 0) > start
    ]


def resolve_statuses(
    svc: Any,
    status_map: dict[str, LedgerRowStatus],
    current_hist: dict[str, str | None],
    *,
    check_freshness: bool,
    budget: FetchBudget,
) -> dict[str, LedgerStatus]:
    """Ledger status for every thread in ``current_hist`` (id -> historyId).

    Only threads whose historyId moved since curation are checked. The
    history delta rules out those with no new message; the rest are fetched
    in batched ``format=minimal`` calls (labels + internalDate, no headers),
    newest first while the budget lasts. Any left unfetched read as stale.
    """
    moved = [
        tid
        for tid, hist in current_hist.items()
        if check_freshness
        and (row := status_map.get(tid)) is not None
        and row.state != CurationState.pending
        and _history_moved(row, hist)
    ]
    to_fetch = _may_have_new_mail(svc, moved, status_map) if moved else []
    nothing_new = set(moved) - set(to_fetch)
    to_fetch = budget.take(to_fetch)
    fetched = _batch_get_threads(svc, to_fetch, fmt="minimal") if to_fetch else {}
    newest = {
        tid: newest_incoming_at(thread.get("messages") or [])
        for tid, thread in fetched.items()
    }
    return {
        tid: LedgerStatus.curated
        if tid in nothing_new
        else ledger_status_for(
            status_map.get(tid),
            hist,
            newest.get(tid),
            check_freshness=check_freshness,
        )
        for tid, hist in current_hist.items()
    }


class BeyondScan:
    """Status of ledger threads outside the capped inbox scan, fetched on demand.

    Label lookups run once per instance (one ``inbox_get_curation`` call), not
    once per batch.
    """

    def __init__(
        self, svc: Any, user_id: str, *, check_freshness: bool, budget: FetchBudget
    ) -> None:
        self._svc = svc
        self._user_id = user_id
        self._check_freshness = check_freshness
        self._budget = budget
        self._labels: tuple[dict[str, str], str | None] | None = None

    def statuses(self, thread_ids: list[str]) -> dict[str, LedgerStatus]:
        """Status per still-triageable thread; threads that left are absent."""
        if not thread_ids:
            return {}
        if self._labels is None:
            label_id_to_name, _ = _build_label_lookups(self._svc)
            self._labels = (label_id_to_name, _find_mcp_done_label(self._svc))
        label_id_to_name, done_label_id = self._labels
        to_fetch = self._budget.take(thread_ids)
        fetched = (
            _batch_get_threads(self._svc, to_fetch, fmt="minimal") if to_fetch else {}
        )
        rows = load_status_map(self._user_id, thread_ids)
        out: dict[str, LedgerStatus] = {}
        for tid in thread_ids:
            thread = fetched.get(tid)
            if thread is None:
                # Not fetched (failed, or over budget), so membership is
                # unknown: surface the row for another look rather than hide it.
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
