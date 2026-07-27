"""Gmail drafts + compose + send services.

All services here are headless: pure sync functions that take a Pydantic
input model and return a Pydantic output model. UI/enhancer affordances
(elicitation, MCP Apps, etc.) live in ``mcp_server/enhancers`` and never
touch this module.

``GmailNotConnectedError`` propagates from ``_get_gmail_client`` when the
user has no active token row; the FastMCP factory surfaces it as
``isError: true`` automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import formataddr
from typing import Any

from loguru import logger as log

from models.curation import CurationState
from models.gmail import (
    AttachmentInput,
    AttachmentUpload,
    GmailComposeInput,
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetDraftInput,
    GmailListDraftsInput,
    GmailListDraftsResult,
    GmailReplyInput,
    GmailSendInput,
    GmailSendResult,
    GmailUpdateDraftInput,
    _UnsetType,
    unset_to,
)
from models.gmail import (
    GmailDraftSummary as _DraftSummary,
)
from services import service
from services.curation_ledger import mark_state_best_effort
from services.gmail_draft_helpers import (
    _draft_resource_to_model,
    _fetch_draft_model,
    _rebuild_draft,
    _resolve_inline_images,
    _resolve_update_attachments,
    draft_message_body,
)
from services.gmail_svc import (
    _account_email,
    _addresses,
    _build_raw_message,
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _inputs_to_uploads(
    attachments: list[AttachmentInput] | None,
) -> list[AttachmentUpload] | None:
    """Normalize caller-supplied ``AttachmentInput``s to the upload shape."""
    if not attachments:
        return None
    return [
        AttachmentUpload(
            filename=a.filename, mime_type=a.mime_type, data_base64=a.data_base64
        )
        for a in attachments
    ]


def _draft_summary_from_metadata(draft: dict[str, Any]) -> _DraftSummary:
    msg = draft.get("message") or {}
    headers = _headers_to_dict((msg.get("payload") or {}).get("headers"))
    updated_at: datetime | None = None
    internal_date = msg.get("internalDate")
    if internal_date is not None:
        try:
            updated_at = datetime.fromtimestamp(int(internal_date) / 1000.0, tz=UTC)
        except (TypeError, ValueError):
            updated_at = None
    return _DraftSummary(
        draft_id=draft.get("id") or "",
        to=headers.get("to"),
        subject=headers.get("subject"),
        snippet=msg.get("snippet"),
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_list_drafts",
    description="List the user's Gmail drafts",
    input_model=GmailListDraftsInput,
    output_model=GmailListDraftsResult,
)
def gmail_list_drafts(input: GmailListDraftsInput) -> GmailListDraftsResult:
    """Return up to ``input.limit`` drafts with To/Subject metadata."""
    svc = _get_gmail_client(input.user_id)
    listing = svc.users().drafts().list(userId="me", maxResults=input.limit).execute()
    draft_ids = [
        stub["id"] for stub in (listing.get("drafts", []) or []) if stub.get("id")
    ]
    if not draft_ids:
        return GmailListDraftsResult(drafts=[])

    fetched: dict[str, dict] = {}
    batch = svc.new_batch_http_request()
    for did in draft_ids:
        req = (
            svc.users()
            .drafts()
            .get(
                userId="me",
                id=did,
                format="metadata",
                metadataHeaders=["To", "Subject"],
            )
        )

        def _cb(
            request_id: str, response: Any, exception: Any, _did: str = did
        ) -> None:
            if exception is None:
                fetched[_did] = response

        batch.add(req, callback=_cb)
    batch.execute()

    summaries: list[_DraftSummary] = []
    for did in draft_ids:
        meta = fetched.get(did)
        if meta:
            summaries.append(_draft_summary_from_metadata(meta))
    return GmailListDraftsResult(drafts=summaries)


@service(
    name="gmail_get_draft",
    description="Fetch a single Gmail draft by id",
    input_model=GmailGetDraftInput,
    output_model=GmailDraft,
)
def gmail_get_draft(input: GmailGetDraftInput) -> GmailDraft:
    svc = _get_gmail_client(input.user_id)
    draft = (
        svc.users()
        .drafts()
        .get(userId="me", id=input.draft_id, format="full")
        .execute()
    )
    return _draft_resource_to_model(draft)


@service(
    name="gmail_update_draft",
    description=(
        "Patch fields on an existing Gmail draft and open an interactive "
        "composer UI. Non-destructive by default: any field you OMIT is left "
        "unchanged on the draft, and a field set to null is CLEARED - this "
        "holds for to, cc, bcc, subject, body, and attachments. Omit "
        "'attachments' to keep every existing file untouched (so you can edit "
        "the body repeatedly without re-uploading); pass null or [] to drop "
        "them all. 'attachments' may mix new uploads (filename + mime_type + "
        "data_base64) with references to existing files ({attachment_id}) taken "
        "from a prior response, letting you preserve specific files by id. To "
        "add or remove a single file without touching the body, prefer "
        "gmail_add_attachment / gmail_remove_attachment. The returned draft "
        "echoes the saved state (recipients, subject, body_preview, and the "
        "full attachment list with ids/filenames/sizes). ALWAYS call this tool "
        "to write or edit draft content - NEVER compose email text as plain "
        "chat text. Pass your composed text in the 'body' parameter. Keep your "
        "chat response to one brief sentence since the user can edit in the UI."
    ),
    input_model=GmailUpdateDraftInput,
    output_model=GmailDraft,
    mutating=True,
)
def gmail_update_draft(input: GmailUpdateDraftInput) -> GmailDraft:
    """Patch a draft non-destructively: omitted fields stay, null clears them.

    Distinguishes "omitted" from "explicit null" via the ``UNSET`` sentinel
    default (``model_fields_set`` cannot, over MCP - see ``_UnsetType``) so a
    caller can change just the body without disturbing recipients, subject, or
    attachments. Because Gmail's ``drafts().update`` replaces the entire MIME
    message, existing attachments are re-downloaded and re-attached unless the
    caller explicitly clears or overrides them.
    """
    svc = _get_gmail_client(input.user_id)
    current = (
        svc.users()
        .drafts()
        .get(userId="me", id=input.draft_id, format="full")
        .execute()
    )
    message = current.get("message") or {}
    parsed = _parse_message_resource(message)
    message_id = message.get("id") or parsed.get("message_id") or ""

    to = unset_to(input.to, parsed.get("to")) or ""
    subject = unset_to(input.subject, parsed.get("subject")) or ""
    cc = unset_to(input.cc, parsed.get("cc"))
    bcc = unset_to(input.bcc, parsed.get("bcc"))

    # When the caller sets body, it replaces the content (plain text, no HTML).
    # When omitted, preserve whatever representation the draft already had -
    # including an HTML-only body (and its inline cid: images), which would
    # otherwise be erased. Replacing the body with plain text orphans those
    # images, so they are dropped along with the HTML.
    if not isinstance(input.body, _UnsetType):
        body = input.body or ""
        body_html = None
        inline_images = []
    else:
        body = parsed.get("body_text") or ""
        body_html = parsed.get("body_html")
        inline_images = (
            _resolve_inline_images(svc, message_id, parsed) if body_html else []
        )

    attachment_uploads = _resolve_update_attachments(svc, message_id, parsed, input)

    return _rebuild_draft(
        svc,
        draft_id=input.draft_id,
        parsed=parsed,
        to=to,
        subject=subject,
        body=body,
        body_html=body_html,
        cc=cc,
        bcc=bcc,
        attachment_uploads=attachment_uploads,
        in_reply_to=parsed.get("in_reply_to"),
        references=parsed.get("references"),
        inline_images=inline_images,
    )


@service(
    name="gmail_compose",
    description=(
        "Create a new Gmail draft from the given fields and open an interactive "
        "composer UI. Returns the draft's actual saved state - draft_id, "
        "thread_id, recipients, subject, a body_preview, and the attachment list "
        "(each with attachment_id, filename, mime_type, size_bytes) - so you can "
        "verify what was saved without a follow-up gmail_get_draft. To edit it "
        "afterward use gmail_update_draft, which preserves omitted fields and "
        "keeps attachments unless you clear them. ALWAYS use this tool instead "
        "of composing email text in chat - it creates a real Gmail draft where "
        "the user can review, edit, and send. When an interactive UI is rendered "
        "alongside the result, keep your text response brief since the user can "
        "edit in the UI."
    ),
    input_model=GmailComposeInput,
    output_model=GmailDraft,
    mutating=True,
)
def gmail_compose(input: GmailComposeInput) -> GmailDraft:
    svc = _get_gmail_client(input.user_id)
    raw = _build_raw_message(
        to=input.to,
        subject=input.subject,
        body=input.body,
        cc=input.cc,
        bcc=input.bcc,
        in_reply_to_thread_id=input.in_reply_to_thread_id,
        attachments=_inputs_to_uploads(input.attachments),
    )
    body_dict = draft_message_body(raw, input.in_reply_to_thread_id)
    created = svc.users().drafts().create(userId="me", body=body_dict).execute()
    log.debug("Created Gmail draft id={}", created.get("id"))
    # Gmail's create response carries only a minimal message (id/threadId), so
    # re-fetch at format=full to echo the real saved state (recipients, body,
    # attachment ids) the tool contract promises.
    return _fetch_draft_model(svc, created.get("id") or "")


@service(
    name="gmail_send",
    description="Send a previously-composed Gmail draft",
    input_model=GmailSendInput,
    output_model=GmailSendResult,
    mutating=True,
)
def gmail_send(input: GmailSendInput) -> GmailSendResult:
    svc = _get_gmail_client(input.user_id)
    sent = svc.users().drafts().send(userId="me", body={"id": input.draft_id}).execute()
    return GmailSendResult(
        message_id=sent.get("id") or "",
        thread_id=sent.get("threadId"),
        sent_at=datetime.now(UTC),
    )


@service(
    name="gmail_discard_draft",
    description="Delete a Gmail draft by id",
    input_model=GmailDiscardDraftInput,
    output_model=GmailDiscardDraftResult,
    mutating=True,
)
def gmail_discard_draft(input: GmailDiscardDraftInput) -> GmailDiscardDraftResult:
    """Delete a draft. Gmail's ``drafts().delete`` returns no body on success."""
    svc = _get_gmail_client(input.user_id)
    svc.users().drafts().delete(userId="me", id=input.draft_id).execute()
    log.debug("Discarded Gmail draft id={}", input.draft_id)
    return GmailDiscardDraftResult(discarded=True)


# ---------------------------------------------------------------------------
# Reply helper (creates a draft for an existing thread)
# ---------------------------------------------------------------------------


def _select_reply_recipient(
    messages: list[dict[str, Any]], self_email: str | None
) -> str:
    """Choose the default ``To`` for a reply to ``messages`` (thread order).

    A reply should reach the other party, not the account owner. Walk the
    thread newest-first and reply to the sender (``Reply-To`` falling back to
    ``From``, RFC 5322 5.2.2) of the most recent message NOT sent by the owner.
    If every message was sent by the owner - e.g. they sent the last message
    and are now following up - fall back to the recipients (``To`` + ``Cc``) of
    the latest message with the owner removed, so the reply still addresses the
    people in the conversation rather than the owner themselves.

    When ``self_email`` is unknown (None) no address matches "self", so this
    reduces to the historical behavior: reply to the last message's sender.
    """
    self_norm = self_email.strip().lower() if self_email else None

    def _is_self(addr: str) -> bool:
        return self_norm is not None and addr.strip().lower() == self_norm

    for msg in reversed(messages):
        headers = _headers_to_dict((msg.get("payload") or {}).get("headers"))
        # Ownership is decided by ``From`` - who actually sent the message - not
        # ``Reply-To``. An owner-sent message may carry a non-self ``Reply-To``
        # (e.g. "reply to my assistant"); it must still count as the owner's so
        # the loop skips past it to the real other party. Messages with no
        # attributable sender are skipped too.
        from_addresses = _addresses(headers.get("from"))
        if not from_addresses or all(_is_self(addr) for _, addr in from_addresses):
            continue
        # Incoming message: reply to the address its sender asked for - the
        # ``Reply-To`` if present (RFC 5322 5.2.2), else ``From`` - verbatim.
        return headers.get("reply-to") or headers.get("from") or ""

    # Every message in the thread was sent by the account owner: reply to the
    # people the latest message was addressed to (To + Cc), minus self.
    last_headers = _headers_to_dict((messages[-1].get("payload") or {}).get("headers"))
    recipients: list[str] = []
    seen: set[str] = set()
    for field in ("to", "cc"):
        for name, addr in _addresses(last_headers.get(field)):
            key = addr.lower()
            if _is_self(addr) or key in seen:
                continue
            seen.add(key)
            recipients.append(formataddr((name, addr)))
    return ", ".join(recipients)


@service(
    name="gmail_reply_to_thread",
    description="Create a reply draft on an existing Gmail thread. ALWAYS use this tool instead of composing reply text in chat - it creates a real Gmail draft and opens an interactive composer UI where the user can review, edit, and send. Pass your drafted reply in the 'body' parameter. Recipients are yours to control: pass 'to', 'cc', and/or 'bcc' (each a comma-separated address list) to set them explicitly. If you omit 'to', it defaults to the other party in the thread (never the account owner); omitted 'cc'/'bcc' are left unset. If every message in the thread is yours (no other participant to reply to), you must pass 'to' explicitly or the call errors. When an interactive UI is rendered alongside the result, keep your text response brief since the user can edit in the UI.",
    input_model=GmailReplyInput,
    output_model=GmailDraft,
    mutating=True,
)
def gmail_reply_to_thread(input: GmailReplyInput) -> GmailDraft:
    """Create a reply draft attached to the given thread.

    Recipients are caller-controlled: ``to``/``cc``/``bcc`` are used verbatim
    when supplied. When ``to`` is omitted it defaults to the sender (``Reply-To``
    falling back to ``From``, RFC 5322 5.2.2) of the most recent message the
    account owner did NOT send, so a bare reply reaches the other party rather
    than the owner's own address. If ``to`` is omitted and the thread has no
    other participant to derive one from (every message is the owner's), raises
    ``ValueError`` rather than creating a blank-``To`` draft - pass ``to``
    explicitly for such threads. Prefixes the subject with ``Re:`` unless the
    originating subject already starts with ``Re:``. Propagates the parent's
    ``Message-ID`` as ``In-Reply-To`` and appends to ``References`` so non-Gmail
    MUAs also thread the conversation; Gmail itself uses the ``threadId`` on the
    API wrapper.
    """
    svc = _get_gmail_client(input.user_id)
    thread = (
        svc.users()
        .threads()
        .get(userId="me", id=input.thread_id, format="metadata")
        .execute()
    )
    messages = thread.get("messages") or []
    if not messages:
        raise ValueError(f"Thread {input.thread_id!r} has no messages to reply to")
    last_msg = messages[-1]
    headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
    # Caller-supplied recipients win; only compute the default (and pay the
    # token-row lookup) when the caller left ``to`` unset.
    if input.to is not None:
        to = input.to
    else:
        to = _select_reply_recipient(messages, _account_email(input.user_id))
        if not to:
            # Every message is from the owner and the thread names no other
            # participant, so there is nobody to reply to. Fail clearly instead
            # of creating a draft with a blank (malformed) To header.
            raise ValueError(
                f"Cannot determine a reply recipient for thread "
                f"{input.thread_id!r}: every message is from you and the thread "
                "has no other participants. Pass 'to' explicitly."
            )
    orig_subject = headers.get("subject") or ""
    if input.subject is not None:
        subject = input.subject
    elif orig_subject.lower().startswith("re:"):
        subject = orig_subject
    else:
        subject = f"Re: {orig_subject}" if orig_subject else "Re:"
    body = input.body if input.body is not None else ""

    parent_message_id = headers.get("message-id")
    parent_references = headers.get("references")
    in_reply_to = parent_message_id
    if parent_message_id and parent_references:
        references = f"{parent_references} {parent_message_id}"
    else:
        references = parent_references or parent_message_id

    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        cc=input.cc,
        bcc=input.bcc,
        in_reply_to_thread_id=input.thread_id,
        in_reply_to=in_reply_to,
        references=references,
        attachments=_inputs_to_uploads(input.attachments),
    )
    created = (
        svc.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": raw, "threadId": input.thread_id}},
        )
        .execute()
    )
    log.debug("Created Gmail reply draft id={}", created.get("id"))
    # A reply draft is an action on the thread: reflect it in the ledger so the
    # triage view shows the thread as handled (with its draft) rather than still
    # needing a reply. Best-effort - a DB hiccup must not fail draft creation.
    mark_state_best_effort(
        input.user_id,
        input.thread_id,
        CurationState.acted,
        draft_id=created.get("id"),
    )
    # Re-fetch at format=full: the create response omits the saved recipients,
    # subject, and body, so echoing it directly would return all-null.
    return _fetch_draft_model(svc, created.get("id") or "")
