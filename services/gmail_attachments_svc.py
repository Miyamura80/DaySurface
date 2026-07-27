"""Orthogonal Gmail attachment services: add / remove a single file.

These are deliberately separate from ``gmail_update_draft`` so a content edit
never has to think about files and a file edit never touches body, subject, or
recipients. Both return the draft's resulting attachment list so a caller can
verify the file set without a follow-up ``gmail_get_draft``.
"""

from __future__ import annotations

from typing import Any

from models.gmail import (
    AttachmentUpload,
    GmailAddAttachmentInput,
    GmailDraftAttachmentsResult,
    GmailRemoveAttachmentInput,
)
from services import service
from services.gmail_draft_helpers import (
    _attachment_not_found,
    _current_attachments,
    _existing_to_upload,
    _rebuild_draft,
    _resolve_inline_images,
)
from services.gmail_svc import _get_gmail_client, _parse_message_resource


def _load_draft_for_attachment_edit(
    svc: Any, draft_id: str
) -> tuple[dict[str, Any], str]:
    """Fetch a draft and return its parsed message plus the message id."""
    current = (
        svc.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    )
    message = current.get("message") or {}
    parsed = _parse_message_resource(message)
    message_id = message.get("id") or parsed.get("message_id") or ""
    return parsed, message_id


def _rebuild_preserving_content(
    svc: Any,
    *,
    draft_id: str,
    message_id: str,
    parsed: dict[str, Any],
    attachment_uploads: list[AttachmentUpload],
) -> GmailDraftAttachmentsResult:
    """Rewrite a draft with a new file set, leaving content fields verbatim.

    Every content field is sourced from the current draft: plain and HTML body
    (with its inline cid: images), recipients (including Bcc), and
    reply-threading headers - so an attachment edit never disturbs the message
    itself.
    """
    inline_images = (
        _resolve_inline_images(svc, message_id, parsed)
        if parsed.get("body_html")
        else []
    )
    draft = _rebuild_draft(
        svc,
        draft_id=draft_id,
        parsed=parsed,
        to=parsed.get("to") or "",
        subject=parsed.get("subject") or "",
        body=parsed.get("body_text") or "",
        body_html=parsed.get("body_html"),
        cc=parsed.get("cc"),
        bcc=parsed.get("bcc"),
        attachment_uploads=attachment_uploads,
        in_reply_to=parsed.get("in_reply_to"),
        references=parsed.get("references"),
        inline_images=inline_images,
    )
    return GmailDraftAttachmentsResult(
        draft_id=draft.draft_id, attachments=draft.attachments
    )


@service(
    name="gmail_add_attachment",
    description=(
        "Attach one file to an existing Gmail draft and return the draft's full "
        "attachment list (each with attachment_id, filename, mime_type, "
        "size_bytes). Only the attachments change - body, subject, and "
        "recipients are preserved exactly. Pass the file as 'attachment' "
        "(filename + mime_type + base64 data_base64)."
    ),
    input_model=GmailAddAttachmentInput,
    output_model=GmailDraftAttachmentsResult,
    mutating=True,
)
def gmail_add_attachment(
    input: GmailAddAttachmentInput,
) -> GmailDraftAttachmentsResult:
    """Append a file to a draft, leaving content fields untouched."""
    svc = _get_gmail_client(input.user_id)
    parsed, message_id = _load_draft_for_attachment_edit(svc, input.draft_id)

    uploads = [
        _existing_to_upload(svc, message_id, a) for a in _current_attachments(parsed)
    ]
    uploads.append(
        AttachmentUpload(
            filename=input.attachment.filename,
            mime_type=input.attachment.mime_type,
            data_base64=input.attachment.data_base64,
        )
    )
    return _rebuild_preserving_content(
        svc,
        draft_id=input.draft_id,
        message_id=message_id,
        parsed=parsed,
        attachment_uploads=uploads,
    )


@service(
    name="gmail_remove_attachment",
    description=(
        "Remove one file from a Gmail draft by its attachment_id and return the "
        "draft's remaining attachment list. Only the attachments change - body, "
        "subject, and recipients are preserved exactly. The attachment_id comes "
        "from any prior draft response or gmail_get_draft."
    ),
    input_model=GmailRemoveAttachmentInput,
    output_model=GmailDraftAttachmentsResult,
    mutating=True,
)
def gmail_remove_attachment(
    input: GmailRemoveAttachmentInput,
) -> GmailDraftAttachmentsResult:
    """Drop a file from a draft by id, leaving content fields untouched."""
    svc = _get_gmail_client(input.user_id)
    parsed, message_id = _load_draft_for_attachment_edit(svc, input.draft_id)

    current = _current_attachments(parsed)
    remaining = [a for a in current if a.get("attachment_id") != input.attachment_id]
    if len(remaining) == len(current):
        raise _attachment_not_found(input.attachment_id, input.draft_id)
    uploads = [_existing_to_upload(svc, message_id, a) for a in remaining]
    return _rebuild_preserving_content(
        svc,
        draft_id=input.draft_id,
        message_id=message_id,
        parsed=parsed,
        attachment_uploads=uploads,
    )
