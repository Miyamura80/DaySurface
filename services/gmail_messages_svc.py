"""Gmail inbox / thread / attachment services.

All services here are headless: pure sync functions that take a Pydantic
input model and return a Pydantic output model. UI/enhancer affordances
live in ``mcp_server/enhancers`` and never touch this module.

The curated-inbox ranking lives in ``services.gmail_curate_svc``, which
reuses ``_internal_date_to_dt`` and ``_BATCH_CHUNK_SIZE`` from here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger as log
from pydantic import BaseModel

from common import global_config
from models.curation import CurationState
from models.gmail import (
    GmailAttachmentData,
    GmailDraft,
    GmailGetAttachmentInput,
    GmailGetThreadInput,
    GmailListInboxInput,
    GmailListInboxResult,
    GmailMessageSummary,
    GmailThread,
    GmailThreadMessage,
)
from services import service
from services.curation_ledger import mark_state_best_effort
from services.gmail_draft_helpers import (
    _draft_resource_to_model,
    find_draft_id_for_thread,
)
from services.gmail_svc import (
    GmailAttachmentTooLargeError,
    _b64url_to_std,
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)


# Inputs/outputs for thread-modify services kept inline (small,
# transport-agnostic) so models/gmail.py doesn't need to grow for these
# small toggles. Promote to models/gmail.py if reused elsewhere.
class GmailThreadModifyInput(BaseModel):
    user_id: str = ""
    thread_id: str


class GmailMarkReadResult(BaseModel):
    marked_read: bool


class GmailArchiveResult(BaseModel):
    archived: bool


class GmailMarkDoneResult(BaseModel):
    marked_done: bool
    label_id: str | None = None


class GmailUnmarkDoneResult(BaseModel):
    unmarked_done: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _internal_date_to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError):
        return None


def _message_summary_from_metadata(msg: dict[str, Any]) -> GmailMessageSummary:
    headers = _headers_to_dict((msg.get("payload") or {}).get("headers"))
    return GmailMessageSummary.model_validate(
        {
            "message_id": msg.get("id") or "",
            "thread_id": msg.get("threadId"),
            "subject": headers.get("subject"),
            "from": headers.get("from"),
            "snippet": msg.get("snippet"),
            "date": _internal_date_to_dt(msg.get("internalDate")),
        }
    )


# Quoted-history markers, mirrored from the inbox app's splitHtmlAtQuote /
# splitTextAtQuote (mcp_server/apps/gmail_inbox/src/Inbox.tsx) so the server-side
# collapse matches what the reader UI hides behind its "..." toggle.
_HTML_QUOTE_MARKERS = (
    '<div class="gmail_quote"',
    '<blockquote class="gmail_quote"',
    '<div class=3D"gmail_quote"',
)
_ON_WROTE_HTML_RE = re.compile(
    r"(<br\s*/?>[\s\S]{0,20}?On\s.{10,80}\s+wrote:\s*<br\s*/?>)", re.IGNORECASE
)
_ON_WROTE_TEXT_RE = re.compile(r"^On .{10,80} wrote:\s*$")


def _strip_quoted_html(html: str) -> str:
    """Return ``html`` with the quoted prior-message chain removed."""
    for marker in _HTML_QUOTE_MARKERS:
        idx = html.find(marker)
        if idx > 0:
            return html[:idx]
    m = _ON_WROTE_HTML_RE.search(html)
    if m and m.start() > 50:
        return html[: m.start()]
    return html


def _strip_quoted_text(text: str) -> str:
    """Return plain-text ``text`` with the quoted prior-message chain removed."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i > 0 and _ON_WROTE_TEXT_RE.match(line):
            return "\n".join(lines[:i])
    first_q = -1
    for i, line in enumerate(lines):
        if line.startswith(">"):
            if first_q == -1:
                first_q = i
        elif first_q != -1:
            break
    if first_q > 0 and len(lines) - first_q >= 3:
        return "\n".join(lines[:first_q])
    return text


def _thread_message_from_parsed(
    parsed: dict[str, Any],
    *,
    include_attachment_data: bool,
    strip_quoted_replies: bool,
) -> GmailThreadMessage:
    attachments = parsed.get("attachments") or []
    if not include_attachment_data:
        # Drop the base64 blobs but keep every locator (attachment_id, filename,
        # mime_type, size, content_id) so callers can fetch bytes on demand.
        attachments = [{**a, "data": None} for a in attachments]

    body_text = parsed.get("body_text")
    body_html = parsed.get("body_html")
    if strip_quoted_replies:
        if body_text:
            body_text = _strip_quoted_text(body_text)
        if body_html:
            body_html = _strip_quoted_html(body_html)

    return GmailThreadMessage.model_validate(
        {
            "message_id": parsed.get("message_id") or "",
            "from": parsed.get("from"),
            "to": parsed.get("to"),
            "cc": parsed.get("cc"),
            "date": parsed.get("date"),
            "subject": parsed.get("subject"),
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
        }
    )


_CID_RE = re.compile(r'(?:src|background)\s*=\s*["\']cid:([^"\']+)["\']', re.IGNORECASE)


def _resolve_inline_images(svc: Any, message_id: str, parsed: dict[str, Any]) -> None:
    """Fetch missing image data and replace cid: references in HTML with data URIs."""
    attachments: list[dict[str, Any]] = parsed.get("attachments") or []
    cid_map: dict[str, str] = {}

    for att in attachments:
        mime = att.get("mime_type") or ""
        if not mime.startswith("image/"):
            continue
        cid = att.get("content_id")
        aid = att.get("attachment_id")

        if cid and not att.get("data") and aid:
            try:
                resp = (
                    svc.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=aid)
                    .execute()
                )
                att["data"] = _b64url_to_std(resp.get("data", ""))
            except Exception:  # noqa: BLE001  # best-effort image fetch
                continue

        if cid and att.get("data"):
            cid_map[cid] = f"data:{mime};base64,{att['data']}"

    html = parsed.get("body_html")
    if html and cid_map:

        def _replace_cid(match: re.Match[str]) -> str:
            attr = match.group(0).split("=")[0]
            cid_ref = match.group(1)
            data_uri = cid_map.get(cid_ref)
            if data_uri:
                return f'{attr}="{data_uri}"'
            return match.group(0)

        parsed["body_html"] = _CID_RE.sub(_replace_cid, html)


_BATCH_CHUNK_SIZE = 50  # Gmail batch API limit is 100; stay well under


def _batch_get_messages(
    svc: Any,
    message_ids: list[str],
    *,
    fmt: str = "metadata",
    metadata_headers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch multiple messages in a single batched HTTP request."""
    results: dict[str, dict[str, Any]] = {}

    for offset in range(0, len(message_ids), _BATCH_CHUNK_SIZE):
        chunk = message_ids[offset : offset + _BATCH_CHUNK_SIZE]
        batch = svc.new_batch_http_request()
        for mid in chunk:
            kwargs: dict[str, Any] = {"userId": "me", "id": mid, "format": fmt}
            if metadata_headers:
                kwargs["metadataHeaders"] = metadata_headers
            req = svc.users().messages().get(**kwargs)

            def _cb(
                request_id: str, response: Any, exception: Any, _mid: str = mid
            ) -> None:
                if exception is not None:
                    log.warning(
                        "Batch message fetch failed for {}: {}", _mid, exception
                    )
                    return
                results[_mid] = response

            batch.add(req, callback=_cb)
        batch.execute()

    return results


_MCP_DONE_LABEL_NAME = "MCP/Done"


def _find_mcp_done_label(svc: Any) -> str | None:
    """Return the label ID for ``MCP/Done`` if it exists, else ``None``."""
    labels_response = svc.users().labels().list(userId="me").execute()
    for label in labels_response.get("labels", []):
        if label.get("name") == _MCP_DONE_LABEL_NAME:
            return label["id"]
    return None


def _get_or_create_mcp_done_label(svc: Any) -> str:
    """Return the label ID for ``MCP/Done``, creating it if absent."""
    # Deliberate deferral: the Google SDK is heavy - only load it when a Gmail
    # API call is actually made, not at service discovery / module import.
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    existing = _find_mcp_done_label(svc)
    if existing is not None:
        return existing

    try:
        created = (
            svc.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": _MCP_DONE_LABEL_NAME,
                    "labelListVisibility": "labelHide",
                    "messageListVisibility": "hide",
                },
            )
            .execute()
        )
        return created["id"]
    except HttpError as exc:
        if exc.resp.status == 409:
            existing = _find_mcp_done_label(svc)
            if existing is not None:
                return existing
        raise


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_list_inbox",
    description="List recent inbox messages, optionally filtered by a Gmail search query. When the user asks to find or open a specific email, ALWAYS follow up by calling gmail_get_thread with the thread_id to render the full conversation in an interactive UI.",
    input_model=GmailListInboxInput,
    output_model=GmailListInboxResult,
)
def gmail_list_inbox(input: GmailListInboxInput) -> GmailListInboxResult:
    svc = _get_gmail_client(input.user_id)
    q = f"in:inbox ({input.query})" if input.query else "in:inbox"
    listing = (
        svc.users().messages().list(userId="me", q=q, maxResults=input.limit).execute()
    )
    message_ids = [
        stub["id"] for stub in (listing.get("messages", []) or []) if stub.get("id")
    ]
    if not message_ids:
        return GmailListInboxResult(messages=[])
    fetched = _batch_get_messages(
        svc,
        message_ids,
        metadata_headers=["From", "To", "Subject", "Date"],
    )
    summaries: list[GmailMessageSummary] = []
    for mid in message_ids:
        meta = fetched.get(mid)
        if meta:
            summaries.append(_message_summary_from_metadata(meta))
    return GmailListInboxResult(messages=summaries)


@service(
    name="gmail_get_thread",
    description="Fetch a Gmail thread by id with full message bodies. By default attachment/inline-image bytes are omitted (each attachment still carries filename, mime_type, size, attachment_id) to keep the payload small - fetch a file on demand with gmail_get_attachment. Pass include_attachment_data=true to inline bytes, or strip_quoted_replies=true to drop repeated quoted history. When an interactive UI is rendered alongside the result, keep your text response brief since the user can browse the conversation in the UI.",
    input_model=GmailGetThreadInput,
    output_model=GmailThread,
)
def gmail_get_thread(input: GmailGetThreadInput) -> GmailThread:
    svc = _get_gmail_client(input.user_id)
    thread = (
        svc.users()
        .threads()
        .get(userId="me", id=input.thread_id, format="full")
        .execute()
    )
    # Check for a draft on this thread (best-effort) before building
    # the messages list so we can exclude the draft's underlying message.
    draft: GmailDraft | None = None
    draft_message_id: str | None = None
    # threads().get(format=full) already includes draft messages, so gate the
    # drafts().list scan on the thread actually carrying a DRAFT-labeled
    # message - the common no-draft case then costs zero extra API calls.
    has_draft_message = any(
        "DRAFT" in (m.get("labelIds") or []) for m in thread.get("messages", []) or []
    )
    draft_id = (
        find_draft_id_for_thread(svc, input.thread_id) if has_draft_message else None
    )
    if draft_id:
        try:
            full_draft = (
                svc.users()
                .drafts()
                .get(userId="me", id=draft_id, format="full")
                .execute()
            )
            draft = _draft_resource_to_model(full_draft)
            draft_message_id = (full_draft.get("message") or {}).get("id")
        except Exception:  # noqa: BLE001  # draft lookup is best-effort
            pass

    messages: list[GmailThreadMessage] = []
    for m in thread.get("messages", []) or []:
        msg_id = m.get("id")
        if msg_id and msg_id == draft_message_id:
            continue
        labels = m.get("labelIds") or []
        if "DRAFT" in labels:
            continue
        parsed = _parse_message_resource(m)
        # Inlining cid: images fetches + embeds their base64 into body_html,
        # which is the bulk of a thread's size. Only do it when the caller
        # actually wants the bytes (and only when there's a message id to fetch
        # attachments against).
        if input.include_attachment_data and msg_id:
            _resolve_inline_images(svc, msg_id, parsed)
        messages.append(
            _thread_message_from_parsed(
                parsed,
                include_attachment_data=input.include_attachment_data,
                strip_quoted_replies=input.strip_quoted_replies,
            )
        )

    return GmailThread(
        thread_id=thread.get("id") or input.thread_id,
        messages=messages,
        draft=draft,
    )


def _decoded_size(raw_size: Any, data_base64: str) -> int:
    """Best-effort decoded byte count for an attachment.

    Prefer Gmail's ``size`` metadata (which it may return as a string), and
    fall back to estimating from the base64 payload length when it is missing
    or unparseable - so a missing size can never silently bypass the cap.
    """
    if raw_size is not None:
        try:
            return int(raw_size)
        except (TypeError, ValueError):
            pass
    # Standard base64 encodes 3 bytes per 4 chars; subtract the '=' padding to
    # get the exact decoded length so the cap is applied at neither more nor
    # less than the configured ceiling.
    return (len(data_base64) * 3) // 4 - data_base64.count("=")


@service(
    name="gmail_get_attachment",
    description="Fetch the raw base64 bytes of a single attachment or inline image on a Gmail message, identified by the message_id + attachment_id echoed by gmail_get_thread. Use this to pull one file on demand instead of loading every attachment into the thread payload. data_base64 is raw encoded bytes, not a rendered image - you cannot read an image's contents from it; on vision-capable MCP hosts, image attachments are additionally rendered into context as a viewable image.",
    input_model=GmailGetAttachmentInput,
    output_model=GmailAttachmentData,
)
def gmail_get_attachment(input: GmailGetAttachmentInput) -> GmailAttachmentData:
    svc = _get_gmail_client(input.user_id)
    resp = (
        svc.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=input.message_id, id=input.attachment_id)
        .execute()
    )
    data_base64 = _b64url_to_std(resp.get("data", "") or "")
    # The returned base64 lands directly in the model's context, so refuse files
    # larger than the configured ceiling rather than blowing the context window.
    # Gmail can hand numeric fields back as strings (see _internal_date_to_dt);
    # when the size metadata is missing or unparseable, estimate the decoded
    # size from the payload we already fetched so the cap can't be bypassed.
    size = _decoded_size(resp.get("size"), data_base64)
    max_bytes = global_config.gmail.max_attachment_bytes
    if size > max_bytes:
        raise GmailAttachmentTooLargeError(
            attachment_id=input.attachment_id, size=size, max_bytes=max_bytes
        )
    return GmailAttachmentData(
        message_id=input.message_id,
        attachment_id=input.attachment_id,
        size=size,
        data_base64=data_base64,
    )


@service(
    name="gmail_mark_thread_read",
    description="Mark a Gmail thread as read by removing the UNREAD label",
    input_model=GmailThreadModifyInput,
    output_model=GmailMarkReadResult,
    mutating=True,
)
def gmail_mark_thread_read(input: GmailThreadModifyInput) -> GmailMarkReadResult:
    svc = _get_gmail_client(input.user_id)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
    return GmailMarkReadResult(marked_read=True)


@service(
    name="gmail_archive_thread",
    description="Archive a Gmail thread by removing the INBOX label. Also marks the thread dismissed in the curation ledger. During a triage pass, continue on to the next uncurated or stale thread.",
    input_model=GmailThreadModifyInput,
    output_model=GmailArchiveResult,
    mutating=True,
)
def gmail_archive_thread(input: GmailThreadModifyInput) -> GmailArchiveResult:
    svc = _get_gmail_client(input.user_id)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()
    # Archiving removes the thread from the triageable inbox: the ledger row (if
    # any) is now dismissed. Best-effort - never fail the archive on a DB hiccup.
    mark_state_best_effort(input.user_id, input.thread_id, CurationState.dismissed)
    return GmailArchiveResult(archived=True)


@service(
    name="gmail_mark_thread_done",
    description="Mark a Gmail thread as done by applying the MCP/Done label (hides from curated inbox). Also marks the thread dismissed in the curation ledger. During a triage pass, continue on to the next uncurated or stale thread.",
    input_model=GmailThreadModifyInput,
    output_model=GmailMarkDoneResult,
    mutating=True,
)
def gmail_mark_thread_done(input: GmailThreadModifyInput) -> GmailMarkDoneResult:
    svc = _get_gmail_client(input.user_id)
    label_id = _get_or_create_mcp_done_label(svc)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"addLabelIds": [label_id]},
    ).execute()
    # Marking done hides the thread from the curated inbox: dismiss its row.
    mark_state_best_effort(input.user_id, input.thread_id, CurationState.dismissed)
    return GmailMarkDoneResult(marked_done=True, label_id=label_id)


@service(
    name="gmail_unmark_thread_done",
    description="Remove the MCP/Done label from a thread (undo mark-done)",
    input_model=GmailThreadModifyInput,
    output_model=GmailUnmarkDoneResult,
    mutating=True,
)
def gmail_unmark_thread_done(input: GmailThreadModifyInput) -> GmailUnmarkDoneResult:
    svc = _get_gmail_client(input.user_id)
    label_id = _find_mcp_done_label(svc)
    if label_id is None:
        return GmailUnmarkDoneResult(unmarked_done=True)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": [label_id]},
    ).execute()
    return GmailUnmarkDoneResult(unmarked_done=True)
