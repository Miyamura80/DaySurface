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
from typing import Any

from loguru import logger as log

from models.gmail import (
    GmailComposeInput,
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetDraftInput,
    GmailListDraftsInput,
    GmailListDraftsResult,
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
from services.gmail_draft_helpers import (
    DraftGoneError,
    _fetch_draft_model,
    _get_draft_resource,
    _inputs_to_uploads,
    _rebuild_draft,
    _resolve_inline_images,
    _resolve_update_attachments,
    draft_gone_on_404,
    draft_message_body,
    on_draft,
)
from services.gmail_svc import (
    _build_raw_message,
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
@on_draft
def gmail_get_draft(input: GmailGetDraftInput) -> GmailDraft:
    return _fetch_draft_model(_get_gmail_client(input.user_id), input.draft_id)


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
@on_draft
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
    current = _get_draft_resource(svc, input.draft_id)
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
@on_draft
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
    """Delete a draft. Gmail's ``drafts().delete`` returns no body on success.

    A draft that is already gone (sent or discarded from the composer) counts
    as discarded: the caller's goal state holds either way.
    """
    svc = _get_gmail_client(input.user_id)
    try:
        with draft_gone_on_404(input.draft_id):
            svc.users().drafts().delete(userId="me", id=input.draft_id).execute()
    except DraftGoneError:
        log.debug("Draft id={} was already gone", input.draft_id)
    else:
        log.debug("Discarded Gmail draft id={}", input.draft_id)
    return GmailDiscardDraftResult(discarded=True)
