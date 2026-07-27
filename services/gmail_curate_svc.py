"""Gmail curated-inbox service.

Ranks recent inbox threads by a deterministic importance score (v1):

* +1.0  if it carries ``Needs Reply`` (dominates recency)
* +0.3  if it carries the ``UNREAD`` label
* +0.3  if it carries ``Customer/Prospect``
* +0.25 if it carries ``To Do``
* +0.2  if it carries ``Travel``
* recency: ``0.3 * max(0, 1 - age_hours / 168)`` (linear decay over a week)

Note: ``FYI`` label is intentionally excluded - too noisy to be a useful signal.

Note: Gmail's auto-applied ``IMPORTANT`` label is intentionally excluded -
its classifier is too noisy to be a reliable signal.

Gmail category tabs ``Updates``, ``Promotions``, ``Social``, and ``Forums``
are excluded from the curate query - only Primary tab emails enter the
scoring pipeline. User-applied labels from the classification cronjob
(``Newsletter``, ``Promotion``, ``Marketing``, ``Notifications``,
``Product Updates``, ``Marketing/Webinar``, ``Webinar``, ``Cold Outbound``,
``NPS Survey``, ``Survey``) are also excluded.

Upgrade path: swap the deterministic scorer for a DSPY signature that
ranks ``(subject, snippet, sender, age, labels)`` tuples; the function
shape stays the same so callers are unaffected.

Headless service: a pure sync function taking a Pydantic input and returning
a Pydantic output. UI/enhancer affordances live in ``mcp_server/enhancers``
and never touch this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger as log

from models.gmail import (
    GmailCuratedThread,
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailLabelChip,
)
from services import service
from services.gmail_draft_helpers import draft_thread_map
from services.gmail_messages_svc import _BATCH_CHUNK_SIZE, _internal_date_to_dt
from services.gmail_svc import _get_gmail_client, _headers_to_dict

_USER_LABEL_BOOSTS: dict[str, float] = {
    "Needs Reply": 1.0,
    "Customer/Prospect": 0.30,
    "To Do": 0.25,
    "Travel": 0.20,
}

# Ordered (tuple, not set) so the display-only chips render in a stable order -
# set iteration order isn't guaranteed and would make the UI output vary.
_DISPLAY_ONLY_LABELS: tuple[str, ...] = (
    "FYI",
    "Waiting",
    "Action Required",
    "High Priority",
    "Follow-up",
    "Needs Review",
    "KYC",
    "Fundraising",
)

_EXCLUDE_LABELS: set[str] = {
    "Newsletter",
    "Promotion",
    "Marketing",
    "Notifications",
    "Product Updates",
    "Marketing/Webinar",
    "Webinar",
    "Cold Outbound",
    "NPS Survey",
    "Survey",
}

_ALL_CHIP_LABELS: set[str] = set(_USER_LABEL_BOOSTS) | set(_DISPLAY_ONLY_LABELS)

_ALL_TRACKED_LABELS: set[str] = _ALL_CHIP_LABELS | _EXCLUDE_LABELS

_SYSTEM_LABEL_COLORS: dict[str, tuple[str, str]] = {
    "UNREAD": ("#e8f0fe", "#1a73e8"),
}


def _batch_get_threads(
    svc: Any,
    thread_ids: list[str],
    *,
    fmt: str = "metadata",
    metadata_headers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch multiple threads in a single batched HTTP request.

    Returns a dict mapping thread_id → thread payload. Threads that fail
    (deleted between list and get) are silently skipped.
    """
    results: dict[str, dict[str, Any]] = {}

    for offset in range(0, len(thread_ids), _BATCH_CHUNK_SIZE):
        chunk = thread_ids[offset : offset + _BATCH_CHUNK_SIZE]
        batch = svc.new_batch_http_request()
        for tid in chunk:
            kwargs: dict[str, Any] = {"userId": "me", "id": tid, "format": fmt}
            if metadata_headers:
                kwargs["metadataHeaders"] = metadata_headers
            req = svc.users().threads().get(**kwargs)

            def _cb(
                request_id: str, response: Any, exception: Any, _tid: str = tid
            ) -> None:
                if exception is not None:
                    log.warning("Batch thread fetch failed for {}: {}", _tid, exception)
                    return
                results[_tid] = response

            batch.add(req, callback=_cb)
        batch.execute()

    return results


def _format_recency(age_hours: float) -> str:
    if age_hours < 1:
        return "Just now"
    if age_hours < 24:
        return f"{int(age_hours)}h ago"
    days = age_hours / 24.0
    if days < 7:
        d = int(days)
        return f"{d} day{'s' if d != 1 else ''} ago"
    weeks = days / 7.0
    w = int(weeks)
    return f"{w} week{'s' if w != 1 else ''} ago"


def _score_thread(
    *,
    label_ids: list[str],
    label_names: set[str],
    label_colors: dict[str, tuple[str, str]],
    last_message_at: datetime | None,
    now: datetime,
) -> tuple[float, list[str], list[GmailLabelChip]]:
    """Deterministic v1 importance score; see module docstring."""
    score = 0.0
    reasons: list[str] = []
    chips: list[GmailLabelChip] = []

    if "UNREAD" in label_ids:
        score += 0.3
        bg, text = _SYSTEM_LABEL_COLORS["UNREAD"]
        chips.append(GmailLabelChip(name="Unread", bg_color=bg, text_color=text))

    for name, boost in _USER_LABEL_BOOSTS.items():
        if name in label_names:
            score += boost
            bg, text = label_colors.get(name, ("#f1f3f4", "#444444"))
            chips.append(GmailLabelChip(name=name, bg_color=bg, text_color=text))

    for name in _DISPLAY_ONLY_LABELS:
        if name in label_names:
            bg, text = label_colors.get(name, ("#f1f3f4", "#444444"))
            chips.append(GmailLabelChip(name=name, bg_color=bg, text_color=text))

    if last_message_at is not None:
        age_seconds = (now - last_message_at).total_seconds()
        age_hours = max(0.0, age_seconds / 3600.0)
        recency = 0.3 * max(0.0, 1.0 - age_hours / 168.0)
        if recency > 0:
            score += recency
            reasons.append(_format_recency(age_hours))

    return score, reasons, chips


def _build_label_lookups(
    svc: Any,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Build label ID→name and name→(bg, text) color maps for tracked labels."""
    label_id_to_name: dict[str, str] = {}
    label_colors: dict[str, tuple[str, str]] = {}
    labels_resp = svc.users().labels().list(userId="me").execute()
    for lbl in labels_resp.get("labels", []):
        name = lbl.get("name")
        if name in _ALL_TRACKED_LABELS:
            label_id_to_name[lbl["id"]] = name
            color = lbl.get("color") or {}
            bg = color.get("backgroundColor", "#f1f3f4")
            text = color.get("textColor", "#444444")
            label_colors[name] = (bg, text)
    return label_id_to_name, label_colors


# Base "triageable inbox" query: excludes done threads, Gmail category tabs,
# and user-applied noise labels. Shared by the deterministic curate service and
# the ledger read/search path so both agree on what counts as inbox to triage.
_CURATE_BASE_QUERY = (
    'in:inbox -label:"MCP/Done"'
    " -category:updates -category:promotions -category:social -category:forums"
    " -label:Newsletter -label:Promotion -label:Marketing -label:Notifications"
    ' -label:"Product Updates" -label:"Marketing/Webinar" -label:Webinar'
    ' -label:"Cold Outbound" -label:"NPS Survey" -label:Survey'
)


def build_curate_query(query: str | None = None) -> str:
    """Return the triageable-inbox Gmail query, optionally AND-ed with ``query``."""
    return f"{_CURATE_BASE_QUERY} ({query})" if query else _CURATE_BASE_QUERY


def _thread_has_noise_labels(
    messages: list[dict[str, Any]],
    label_id_to_name: dict[str, str],
) -> bool:
    """Return ``True`` if any message in the thread carries an excluded label."""
    for msg in messages:
        for lid in msg.get("labelIds") or []:
            name = label_id_to_name.get(lid)
            if name and name in _EXCLUDE_LABELS:
                return True
    return False


@service(
    name="gmail_curate_inbox",
    description="Rank recent inbox threads by a deterministic heuristic score and render the inbox dashboard. This is a quick provisional view, NOT the assistant's own triage: for 'what's important / triage my inbox', prefer inbox_get_curation (banked LLM verdicts + coverage), going deeper with inbox_search + inbox_save_curation when coverage shows uncurated/stale threads. When an interactive UI is rendered alongside the result, keep your text response brief (a one-line summary) since the user can browse details in the UI. Only elaborate if the user asks.",
    input_model=GmailCurateInboxInput,
    output_model=GmailCurateInboxResult,
)
def gmail_curate_inbox(input: GmailCurateInboxInput) -> GmailCurateInboxResult:
    svc = _get_gmail_client(input.user_id)
    label_id_to_name, label_colors = _build_label_lookups(svc)
    thread_to_draft = draft_thread_map(svc)

    # Exclude done threads. The mark-done service applies the label "MCP/Done"
    # (see _MCP_DONE_LABEL_NAME in gmail_messages_svc), so the exclusion must use
    # that exact name - "MCP-Done" would not match and done threads would leak in.
    q = build_curate_query(input.query)
    over_fetch = max(input.limit * 3, 30)
    listing = (
        svc.users().threads().list(userId="me", q=q, maxResults=over_fetch).execute()
    )

    now = datetime.now(UTC)

    thread_ids = [
        stub["id"] for stub in (listing.get("threads", []) or []) if stub.get("id")
    ]
    fetched_threads = (
        _batch_get_threads(
            svc,
            thread_ids,
            metadata_headers=["From", "Subject", "Date"],
        )
        if thread_ids
        else {}
    )

    curated: list[GmailCuratedThread] = []

    for thread_id in thread_ids:
        thread = fetched_threads.get(thread_id)
        if thread is None:
            continue

        messages = thread.get("messages") or []
        if not messages:
            continue

        if _thread_has_noise_labels(messages, label_id_to_name):
            continue

        last_msg = messages[-1]
        headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
        last_message_at = _internal_date_to_dt(last_msg.get("internalDate"))
        all_label_ids: set[str] = set()
        for msg in messages:
            all_label_ids.update(msg.get("labelIds") or [])
        label_ids: list[str] = list(all_label_ids)
        label_names = {
            label_id_to_name[lid] for lid in label_ids if lid in label_id_to_name
        }

        score, reasons, chips = _score_thread(
            label_ids=label_ids,
            label_names=label_names,
            label_colors=label_colors,
            last_message_at=last_message_at,
            now=now,
        )

        tid = thread.get("id") or thread_id
        draft_id = thread_to_draft.get(tid)
        curated.append(
            GmailCuratedThread.model_validate(
                {
                    "thread_id": tid,
                    "subject": headers.get("subject"),
                    "from": headers.get("from"),
                    "snippet": last_msg.get("snippet"),
                    "last_message_at": last_message_at,
                    "importance_score": score,
                    "reasons": reasons,
                    "labels": [c.model_dump() for c in chips],
                    "has_draft": draft_id is not None,
                    "draft_id": draft_id,
                }
            )
        )

    curated.sort(key=lambda t: t.importance_score, reverse=True)
    return GmailCurateInboxResult(threads=curated[: input.limit])
