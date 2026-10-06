"""Inbox curation-ledger services (headless, transport-agnostic).

Three tools that turn "triage my inbox" into persistent, incrementally
maintained state instead of a from-scratch recompute:

- ``inbox_get_curation`` - cheap read of banked host-LLM verdicts plus a
  coverage summary (curated / stale / uncurated). No inference, no bodies.
- ``inbox_search`` - headless deep primitive: broad search over recent mail,
  each result annotated with its ledger status so the host focuses on the
  delta (uncurated / stale threads).
- ``inbox_save_curation`` - explicit, mutating write-back that banks the host's
  judgments so the next read is near-zero-token.

Effort is emergent, not partitioned: the host reads coverage, decides how much
of the unknown delta to process, searches + reasons over it, and records the
result. A verdict goes stale only when a new message arrives after it was
banked (see ``services.curation_status``), so a deep pass re-reasons over
threads where someone wrote, not over read/label churn or the user's replies.

The deterministic score from ``gmail_curate_svc`` is reused only as a
provisional prior for *uncurated* threads, subordinate to LLM judgments.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger as log

from models.curation import (
    CoverageSummary,
    GetCurationInput,
    GetCurationResult,
    InboxSearchInput,
    InboxSearchItem,
    InboxSearchResult,
    LedgerStatus,
    SaveCurationInput,
    SaveCurationResult,
)
from services import service
from services.curation_ledger import (
    as_utc,
    list_records,
    load_status_map,
    upsert_judgments,
)
from services.curation_status import (
    is_triageable,
    ledger_status_for,
    newest_incoming_at,
    resolve_statuses,
)
from services.gmail_curate_svc import (
    _batch_get_threads,
    _build_label_lookups,
    _score_thread,
    build_curate_query,
)
from services.gmail_messages_svc import _find_mcp_done_label, _internal_date_to_dt
from services.gmail_svc import _get_gmail_client, _headers_to_dict

# Bound on how many inbox thread stubs the cheap read scans for coverage. The
# curated verdicts users care about are recent; scanning the whole mailbox for
# a coverage count would defeat the "cheap" contract.
_COVERAGE_STUB_CAP = 200
_HISTORY_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Gmail helpers (thread stubs + historyId)
# ---------------------------------------------------------------------------


def _history_str(value: Any) -> str | None:
    """Normalize a Gmail historyId (Gmail may hand it back as int or str)."""
    if value is None:
        return None
    return str(value)


def _list_thread_stubs(svc: Any, q: str, *, cap: int) -> list[dict[str, Any]]:
    """List inbox thread stubs (id + historyId, no bodies) up to ``cap``.

    ``threads.list`` returns a per-thread ``historyId`` on each stub, so this
    single paginated call yields both the current inbox set and each thread's
    freshness watermark without any message fetch.
    """
    stubs: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(stubs) < cap:
        resp = (
            svc.users()
            .threads()
            .list(
                userId="me",
                q=q,
                maxResults=min(_HISTORY_PAGE_SIZE, cap - len(stubs)),
                pageToken=page_token,
            )
            .execute()
        )
        stubs.extend(resp.get("threads", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return stubs


def _search_thread_ids(svc: Any, query: str | None, limit: int) -> list[str]:
    q = build_curate_query(query)
    listing = svc.users().threads().list(userId="me", q=q, maxResults=limit).execute()
    return [stub["id"] for stub in (listing.get("threads", []) or []) if stub.get("id")]


def _mailbox_history_id(svc: Any) -> str | None:
    """Return the mailbox's latest historyId (a future ``since`` watermark)."""
    try:
        profile = svc.users().getProfile(userId="me").execute()
    except Exception as exc:  # noqa: BLE001 - best-effort watermark; never fail search
        log.debug("getProfile failed while reading history watermark: {}", exc)
        return None
    return _history_str(profile.get("historyId"))


def _changed_thread_ids(
    svc: Any, since_history_id: str
) -> tuple[list[str] | None, str | None]:
    """Return ``(changed_thread_ids, latest_history_id)`` via users.history.list.

    Returns ``(None, None)`` when Gmail rejects the start id as too old (HTTP
    404) so the caller can fall back to a normal query.

    Consumes the FULL history delta (every page) before returning, so the
    ``latest_history_id`` watermark reflects exactly what was consumed - it is
    never advanced past changes we didn't return. The delta is not truncated by
    the caller's ``limit``: a recent watermark yields a small delta, and a very
    old one 404s into the query fallback, so returning the complete delta keeps
    incremental sync from permanently skipping overflow threads.

    No ``historyTypes`` filter is applied: a thread's ``historyId`` advances on
    *any* change (new message, label add/remove, read/unread), and a label-only
    change can move a thread into or out of the triageable inbox (archive,
    mark-done) - so the delta must surface every changed thread, not just ones
    with a new message. Freshness is then judged per thread on message arrival
    (``services.curation_status``). Each record's
    ``messages`` field lists every message it touched, capturing label-only
    changes that ``messagesAdded`` would miss.
    """
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    thread_ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None
    latest = since_history_id
    try:
        while True:
            resp = (
                svc.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=since_history_id,
                    pageToken=page_token,
                    maxResults=_HISTORY_PAGE_SIZE,
                )
                .execute()
            )
            latest = _history_str(resp.get("historyId")) or latest
            for record in resp.get("history", []) or []:
                for msg in record.get("messages", []) or []:
                    tid = msg.get("threadId")
                    if tid and tid not in seen:
                        seen.add(tid)
                        thread_ids.append(tid)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        if exc.resp.status == 404:
            return None, None
        raise
    return thread_ids, _history_str(latest)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="inbox_get_curation",
    description=(
        "Read banked inbox triage from the curation ledger. Cheap: no email "
        "bodies are fetched and no reasoning is run - it returns judgments the "
        "assistant already made (bucket, importance, summary, suggested action) "
        "plus a coverage count of curated / stale / uncurated threads in the "
        "inbox. Only threads still in the inbox are returned: threads the user "
        "resolved (gmail_mark_thread_done) or archived are omitted until someone "
        "sends a new message on them. 'stale' means someone else wrote since the "
        "verdict. Call this FIRST for any 'what's important / triage my inbox' "
        "request. If coverage shows many uncurated or stale threads and the user "
        "wants a thorough pass, go deeper with inbox_search + inbox_save_curation; "
        "otherwise answer directly from these banked verdicts."
    ),
    input_model=GetCurationInput,
    output_model=GetCurationResult,
)
def inbox_get_curation(input: GetCurationInput) -> GetCurationResult:
    svc = _get_gmail_client(input.user_id)

    stubs = _list_thread_stubs(svc, build_curate_query(), cap=_COVERAGE_STUB_CAP)
    current_hist: dict[str, str | None] = {
        s["id"]: _history_str(s.get("historyId")) for s in stubs if s.get("id")
    }
    inbox_ids = list(current_hist)
    statuses = resolve_statuses(
        svc,
        load_status_map(input.user_id, inbox_ids),
        current_hist,
        check_freshness=input.check_freshness,
    )

    # Coverage over the scanned inbox vs the whole ledger (independent of the
    # display filter / limit below).
    counts = [statuses[tid] for tid in inbox_ids]
    coverage = CoverageSummary(
        curated=counts.count(LedgerStatus.curated),
        stale=counts.count(LedgerStatus.stale),
        uncurated=counts.count(LedgerStatus.uncurated),
    )

    # The scan stops at the newest _COVERAGE_STUB_CAP inbox threads. When it is
    # full, a row outside it may still be in the inbox, so membership is
    # checked directly instead of assumed.
    scan_full = len(stubs) >= _COVERAGE_STUB_CAP
    only_scanned = not (input.include_inactive or scan_full)
    records = list_records(
        input.user_id,
        bucket=input.bucket.value if input.bucket else None,
        state=input.state.value if input.state else None,
        thread_ids=inbox_ids if only_scanned else None,
        limit=None if scan_full and not input.include_inactive else input.limit,
    )
    kept = []
    for offset in range(0, len(records), _MEMBERSHIP_CHUNK):
        chunk = records[offset : offset + _MEMBERSHIP_CHUNK]
        if scan_full:
            unknown = [r.thread_id for r in chunk if r.thread_id not in statuses]
            statuses.update(
                _statuses_beyond_scan(
                    svc, input.user_id, unknown, check_freshness=input.check_freshness
                )
            )
        for rec in chunk:
            status = statuses.get(rec.thread_id)
            if status is None:
                # Left the triageable inbox (resolved or archived): hidden
                # unless asked for, and then not trustworthy as current.
                if not input.include_inactive:
                    continue
                status = LedgerStatus.stale
            if input.fresh_only and status == LedgerStatus.stale:
                continue
            rec.ledger_status = status
            kept.append(rec)
        if len(kept) >= input.limit:
            break

    return GetCurationResult(records=kept[: input.limit], coverage=coverage)


# Ledger rows checked per membership batch when the inbox scan was full.
_MEMBERSHIP_CHUNK = 50


def _statuses_beyond_scan(
    svc: Any, user_id: str, thread_ids: list[str], *, check_freshness: bool
) -> dict[str, LedgerStatus]:
    """Status of threads outside the inbox scan that are still triageable.

    Threads that left the triageable inbox are absent from the result.
    """
    if not thread_ids:
        return {}
    fetched = _batch_get_threads(svc, thread_ids, fmt="minimal")
    label_id_to_name, _ = _build_label_lookups(svc)
    done_label_id = _find_mcp_done_label(svc)
    rows = load_status_map(user_id, thread_ids)
    out: dict[str, LedgerStatus] = {}
    for tid in thread_ids:
        messages = (fetched.get(tid) or {}).get("messages") or []
        if not messages or not is_triageable(
            messages, done_label_id=done_label_id, label_id_to_name=label_id_to_name
        ):
            continue
        out[tid] = ledger_status_for(
            rows.get(tid),
            _history_str(fetched[tid].get("historyId")),
            newest_incoming_at(messages),
            check_freshness=check_freshness,
        )
    return out


def _search_item(
    tid: str,
    thread: dict[str, Any],
    *,
    status: LedgerStatus,
    label_id_to_name: dict[str, str],
    label_colors: dict[str, tuple[str, str]],
    now: datetime,
) -> InboxSearchItem:
    """Build one annotated search item, scoring a provisional prior if uncurated."""
    messages = thread.get("messages") or []
    last_msg = messages[-1]
    headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
    last_at = _internal_date_to_dt(last_msg.get("internalDate"))

    prior: float | None = None
    if status == LedgerStatus.uncurated:
        all_label_ids: set[str] = set()
        for msg in messages:
            all_label_ids.update(msg.get("labelIds") or [])
        label_ids = list(all_label_ids)
        label_names = {
            label_id_to_name[lid] for lid in label_ids if lid in label_id_to_name
        }
        prior, _, _ = _score_thread(
            label_ids=label_ids,
            label_names=label_names,
            label_colors=label_colors,
            last_message_at=last_at,
            now=now,
        )

    return InboxSearchItem.model_validate(
        {
            "thread_id": tid,
            "subject": headers.get("subject"),
            "from": headers.get("from"),
            "snippet": last_msg.get("snippet"),
            "last_message_at": last_at,
            "ledger_status": status,
            "importance_prior": prior,
        }
    )


@service(
    name="inbox_search",
    description=(
        "Search recent inbox threads headlessly (no UI) when doing a thorough "
        "triage pass - use this to actually look at many emails. Returns thread "
        "summaries (subject, sender, snippet, recency) each annotated with its "
        "ledger status: 'uncurated' / 'stale' threads are the delta worth "
        "reasoning about; 'curated' threads are already banked and can be "
        "skipped. Uncurated threads also carry a provisional heuristic "
        "importance_prior. Pass since_history_id (from a prior result's "
        "current_history_id) to fetch only changed threads. After reasoning over "
        "the results, bank your verdicts with inbox_save_curation."
    ),
    input_model=InboxSearchInput,
    output_model=InboxSearchResult,
)
def inbox_search(input: InboxSearchInput) -> InboxSearchResult:
    svc = _get_gmail_client(input.user_id)
    label_id_to_name, label_colors = _build_label_lookups(svc)
    done_label_id = _find_mcp_done_label(svc)

    current_history_id: str | None = None
    thread_ids: list[str] | None = None
    if input.since_history_id:
        thread_ids, current_history_id = _changed_thread_ids(
            svc, input.since_history_id
        )
    if thread_ids is None:
        # No watermark, or the watermark was too old: normal query.
        thread_ids = _search_thread_ids(svc, input.query, input.limit)

    fetched = (
        _batch_get_threads(
            svc, thread_ids, metadata_headers=["From", "Subject", "Date"]
        )
        if thread_ids
        else {}
    )
    status_map = load_status_map(input.user_id, thread_ids)
    now = datetime.now(UTC)

    items: list[InboxSearchItem] = []
    for tid in thread_ids:
        thread = fetched.get(tid)
        if thread is None or not (thread.get("messages") or []):
            continue
        if not is_triageable(
            thread["messages"],
            done_label_id=done_label_id,
            label_id_to_name=label_id_to_name,
        ):
            continue
        status = ledger_status_for(
            status_map.get(tid),
            _history_str(thread.get("historyId")),
            newest_incoming_at(thread["messages"]),
        )
        items.append(
            _search_item(
                tid,
                thread,
                status=status,
                label_id_to_name=label_id_to_name,
                label_colors=label_colors,
                now=now,
            )
        )

    if current_history_id is None:
        current_history_id = _mailbox_history_id(svc)

    return InboxSearchResult(items=items, current_history_id=current_history_id)


@service(
    name="inbox_save_curation",
    description=(
        "Bank your triage judgments for one or more threads into the curation "
        "ledger so they are not re-reasoned next time. Call this after reading "
        "and reasoning over threads (typically from inbox_search) - pass a batch "
        "of per-thread verdicts (bucket, importance, a short summary, suggested "
        "action, optional reasoning/confidence), and set seen_through to each "
        "thread's last_message_at from inbox_search. The verdict stays valid "
        "until someone else writes on the thread. This is what makes the next "
        "inbox_get_curation near-free."
    ),
    input_model=SaveCurationInput,
    output_model=SaveCurationResult,
    mutating=True,
)
def inbox_save_curation(input: SaveCurationInput) -> SaveCurationResult:
    if not input.judgments:
        return SaveCurationResult(saved=0, thread_ids=[])

    svc = _get_gmail_client(input.user_id)
    thread_ids = [j.thread_id for j in input.judgments]
    # format=minimal returns each thread's current historyId and message
    # labels / internalDate, with no bodies.
    fetched = _batch_get_threads(svc, thread_ids, fmt="minimal")
    history_ids: dict[str, str | None] = {
        tid: _history_str(thread.get("historyId")) for tid, thread in fetched.items()
    }
    seen_through: dict[str, datetime | None] = {}
    for j in input.judgments:
        at_save = newest_incoming_at(
            (fetched.get(j.thread_id) or {}).get("messages") or []
        )
        # Prefer what the host actually read: a message landing between its
        # read and this save is newer than that, so it stays stale. Capping at
        # the save-time value keeps a bogus future timestamp from hiding mail.
        seen_through[j.thread_id] = min(
            filter(None, (as_utc(j.seen_through), at_save)), default=None
        )

    saved = upsert_judgments(
        input.user_id,
        input.judgments,
        history_ids=history_ids,
        seen_through=seen_through,
        curator_version=input.curator_version,
    )
    return SaveCurationResult(saved=len(saved), thread_ids=saved)
