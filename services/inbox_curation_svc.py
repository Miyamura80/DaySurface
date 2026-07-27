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
result. Freshness is tracked against Gmail's ``historyId`` so a deep pass only
re-reasons over new/changed threads.

The deterministic score from ``gmail_curate_svc`` is reused only as a
provisional prior for *uncurated* threads, subordinate to LLM judgments.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger as log

from models.curation import (
    CoverageSummary,
    CurationState,
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
    list_records,
    load_status_map,
    upsert_judgments,
)
from services.gmail_curate_svc import (
    _batch_get_threads,
    _build_label_lookups,
    _score_thread,
    _thread_has_noise_labels,
    build_curate_query,
)
from services.gmail_messages_svc import _find_mcp_done_label, _internal_date_to_dt
from services.gmail_svc import _get_gmail_client, _headers_to_dict

# Gmail's system label id for the inbox. A thread archived out of band loses it,
# so an incremental history delta (which is unfiltered) must re-check it.
_INBOX_LABEL_ID = "INBOX"

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


def _is_stale(current: str | None, stored: str | None) -> bool:
    """A thread is stale iff we can compare watermarks and they differ.

    A missing watermark (either side) means we cannot verify, so we do not
    fabricate staleness - the row is treated as fresh.
    """
    if current is None or stored is None:
        return False
    return current != stored


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
    *any* change (new message, label add/remove, read/unread), and the ledger's
    freshness check treats any such advance as stale - so the delta must surface
    every changed thread, not just ones with a new message. Each record's
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
        "inbox. Call this FIRST for any 'what's important / triage my inbox' "
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

    # Coverage over the whole current inbox vs the whole ledger (independent of
    # the display filter / limit below).
    status_map = load_status_map(input.user_id, inbox_ids)
    curated_ct = stale_ct = uncurated_ct = 0
    for tid in inbox_ids:
        row = status_map.get(tid)
        if row is None or row["state"] == CurationState.pending.value:
            uncurated_ct += 1
        elif input.check_freshness and _is_stale(
            current_hist.get(tid), row["curated_history_id"]
        ):
            stale_ct += 1
        else:
            curated_ct += 1
    coverage = CoverageSummary(
        curated=curated_ct, stale=stale_ct, uncurated=uncurated_ct
    )

    # Display records (filtered / limited), each annotated with freshness.
    records = list_records(
        input.user_id,
        bucket=input.bucket.value if input.bucket else None,
        state=input.state.value if input.state else None,
        limit=input.limit,
    )
    kept = []
    for rec in records:
        if rec.thread_id not in current_hist:
            # Thread left the triageable inbox (archived / done, possibly out of
            # band): the row can't be trusted as current.
            rec.ledger_status = LedgerStatus.stale
        elif input.check_freshness and _is_stale(
            current_hist.get(rec.thread_id), rec.curated_history_id
        ):
            rec.ledger_status = LedgerStatus.stale
        else:
            rec.ledger_status = LedgerStatus.curated
        if input.fresh_only and rec.ledger_status == LedgerStatus.stale:
            continue
        kept.append(rec)

    return GetCurationResult(records=kept, coverage=coverage)


def _ledger_status_for(row: dict | None, current_hist: str | None) -> LedgerStatus:
    """Map a ledger status-row + the thread's current historyId to a status."""
    if row is None or row["state"] == CurationState.pending.value:
        return LedgerStatus.uncurated
    if _is_stale(current_hist, row["curated_history_id"]):
        return LedgerStatus.stale
    return LedgerStatus.curated


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


def _is_triageable(
    messages: list[dict[str, Any]],
    *,
    done_label_id: str | None,
    label_id_to_name: dict[str, str],
) -> bool:
    """Whether a thread still belongs in the triageable inbox.

    The incremental history delta is unfiltered (it returns every changed
    thread, including ones archived or marked done out of band), so results are
    re-checked against the same criteria ``build_curate_query()`` enforces
    server-side: in INBOX, not MCP/Done, and no excluded noise label.
    """
    thread_label_ids: set[str] = set()
    for msg in messages:
        thread_label_ids.update(msg.get("labelIds") or [])
    if _INBOX_LABEL_ID not in thread_label_ids:
        return False
    if done_label_id is not None and done_label_id in thread_label_ids:
        return False
    return not _thread_has_noise_labels(messages, label_id_to_name)


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
        if not _is_triageable(
            thread["messages"],
            done_label_id=done_label_id,
            label_id_to_name=label_id_to_name,
        ):
            continue
        status = _ledger_status_for(
            status_map.get(tid), _history_str(thread.get("historyId"))
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
        "action, optional reasoning/confidence). Each write stamps the thread's "
        "current Gmail historyId so the verdict stays valid until the thread "
        "changes. This is what makes the next inbox_get_curation near-free."
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
    # format=minimal returns each thread's current historyId with no bodies.
    fetched = _batch_get_threads(svc, thread_ids, fmt="minimal")
    history_ids: dict[str, str | None] = {
        tid: _history_str(thread.get("historyId")) for tid, thread in fetched.items()
    }

    saved = upsert_judgments(
        input.user_id,
        input.judgments,
        history_ids=history_ids,
        curator_version=input.curator_version,
    )
    return SaveCurationResult(saved=len(saved), thread_ids=saved)
