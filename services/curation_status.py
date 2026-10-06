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
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models.curation import CurationState, LedgerStatus
from services.curation_ledger import LedgerRowStatus
from services.gmail_curate_svc import _batch_get_threads, _thread_has_noise_labels
from services.gmail_messages_svc import _internal_date_to_dt

_INBOX_LABEL_ID = "INBOX"
_SENT_LABEL_ID = "SENT"
_DRAFT_LABEL_ID = "DRAFT"


def is_incoming(msg: dict[str, Any]) -> bool:
    """A message someone else sent, or one the user sent to themselves."""
    labels = msg.get("labelIds") or []
    if _DRAFT_LABEL_ID in labels:
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
    incremental history delta, or a direct fetch). INBOX / MCP/Done follow
    Gmail's per-message matching (see module docstring). Noise labels exclude
    the whole thread, as ``gmail_curate_inbox`` does.
    """
    in_open_inbox = any(
        _INBOX_LABEL_ID in (labels := msg.get("labelIds") or [])
        and (done_label_id is None or done_label_id not in labels)
        for msg in messages
    )
    return in_open_inbox and not _thread_has_noise_labels(messages, label_id_to_name)


def _history_moved(row: LedgerRowStatus, current_history_id: str | None) -> bool:
    # A row without a stored historyId can't use the fast path: check messages.
    if current_history_id is None:
        return False
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


def resolve_statuses(
    svc: Any,
    status_map: dict[str, LedgerRowStatus],
    current_hist: dict[str, str | None],
    *,
    check_freshness: bool,
) -> dict[str, LedgerStatus]:
    """Ledger status for every thread in ``current_hist`` (id -> historyId).

    Only threads whose historyId moved since curation are fetched, in one
    batched ``format=minimal`` call (labels + internalDate, no headers).
    """
    to_check = [
        tid
        for tid, hist in current_hist.items()
        if check_freshness
        and (row := status_map.get(tid)) is not None
        and row.state != CurationState.pending
        and _history_moved(row, hist)
    ]
    fetched = _batch_get_threads(svc, to_check, fmt="minimal") if to_check else {}
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
        for tid, hist in current_hist.items()
    }
