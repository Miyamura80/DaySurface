"""Ledger freshness: has the conversation moved on since a verdict was banked?

A verdict goes stale when a new message lands after it was saved. Gmail's
per-thread ``historyId`` advances on *any* change (read/unread, a label, a
draft being saved), so it only serves as a free "nothing changed" fast path.
Once it has moved, the thread's newest non-draft message is compared against
the row's ``curated_at`` so label and read-state churn never resurfaces a
thread the user already dealt with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models.curation import CurationState, LedgerStatus
from services.gmail_curate_svc import _batch_get_threads
from services.gmail_messages_svc import _internal_date_to_dt

_DRAFT_LABEL_ID = "DRAFT"


def last_message_at(messages: list[dict[str, Any]]) -> datetime | None:
    """Arrival time of the thread's newest real message (drafts excluded)."""
    latest: datetime | None = None
    for msg in messages:
        if _DRAFT_LABEL_ID in (msg.get("labelIds") or []):
            continue
        at = _internal_date_to_dt(msg.get("internalDate"))
        if at is not None and (latest is None or at > latest):
            latest = at
    return latest


def history_moved(row: dict[str, Any], current_history_id: str | None) -> bool:
    """Whether the thread changed at all since curation (any kind of change).

    A missing watermark on either side means we cannot compare, so it is
    treated as unchanged rather than fabricating staleness.
    """
    stored = row.get("curated_history_id")
    if current_history_id is None or stored is None:
        return False
    return current_history_id != stored


def needs_message_check(
    row: dict[str, Any] | None, current_history_id: str | None
) -> bool:
    """Whether ``ledger_status_for`` needs the thread's newest-message time."""
    return (
        row is not None
        and row["state"] in (CurationState.curated.value, CurationState.acted.value)
        and history_moved(row, current_history_id)
    )


def fetch_last_message_times(
    svc: Any, thread_ids: list[str]
) -> dict[str, datetime | None]:
    """Newest-message time per thread, via one batched metadata fetch."""
    if not thread_ids:
        return {}
    fetched = _batch_get_threads(svc, thread_ids, metadata_headers=["Date"])
    return {
        tid: last_message_at(thread.get("messages") or [])
        for tid, thread in fetched.items()
    }


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite hands timezone-aware columns back naive; they were written as UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def ledger_status_for(
    row: dict[str, Any] | None,
    current_history_id: str | None,
    newest_message_at: datetime | None,
    *,
    check_freshness: bool = True,
) -> LedgerStatus:
    """Freshness of one ledger row for a thread that is in the triageable inbox.

    ``row`` is a ``load_status_map`` entry. Callers only pass threads that are
    currently triageable, which matters for ``dismissed`` rows: a thread marked
    done or archived is back in the inbox only because a new message arrived
    (or the user moved it back), so its old verdict is stale.
    """
    if row is None or row["state"] == CurationState.pending.value:
        return LedgerStatus.uncurated
    if row["state"] == CurationState.dismissed.value:
        return LedgerStatus.stale
    if not check_freshness or not history_moved(row, current_history_id):
        return LedgerStatus.curated
    curated_at = _as_utc(row.get("curated_at"))
    if newest_message_at is None or curated_at is None:
        # The thread changed and we cannot tell whether a message arrived:
        # re-reason it rather than risk hiding a new reply.
        return LedgerStatus.stale
    return (
        LedgerStatus.stale if newest_message_at > curated_at else LedgerStatus.curated
    )
