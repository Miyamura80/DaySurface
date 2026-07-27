"""App-only tools used by the gmail_composer MCP App.

These tools are callable by the iframe via ``mcpApp.callServerTool`` and are
hinted as ``visibility=["app"]`` so well-behaved hosts hide them from the
LLM. ``user_id`` arrives on the wire but is overridden by the authenticated
principal when one is bound; see ``mcp_server/app_tools/_auth_guard.py``.
"""

from mcp_server.app_tools._auth_guard import guard_user_id
from mcp_server.server import mcp
from models.gmail import (
    UNSET,
    AttachmentInput,
    AttachmentReference,
    GmailAttachmentData,
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetAttachmentInput,
    GmailGetDraftInput,
    GmailGetThreadInput,
    GmailSendInput,
    GmailSendResult,
    GmailThread,
    GmailUpdateDraftInput,
    _UnsetType,
)
from services.gmail_drafts_svc import (
    gmail_discard_draft as _gmail_discard_draft,
)
from services.gmail_drafts_svc import (
    gmail_get_draft as _gmail_get_draft,
)
from services.gmail_drafts_svc import (
    gmail_send as _gmail_send,
)
from services.gmail_drafts_svc import (
    gmail_update_draft as _gmail_update_draft,
)
from services.gmail_messages_svc import (
    gmail_get_attachment as _gmail_get_attachment,
)
from services.gmail_messages_svc import (
    gmail_get_thread as _gmail_get_thread,
)

_APP_META = {"ui": {"visibility": ["app"]}}


def _coerce_attachments(
    attachments: list[dict] | None,
) -> list[AttachmentInput | AttachmentReference] | None:
    """Map raw attachment dicts to the right model.

    A dict carrying ``data_base64`` is a new upload (``AttachmentInput``); one
    with only ``attachment_id`` is a by-reference preserve (``AttachmentReference``).
    Coercing everything to ``AttachmentInput`` would crash reference dicts.
    """
    if not attachments:
        return None
    out: list[AttachmentInput | AttachmentReference] = []
    for a in attachments:
        if a.get("data_base64"):
            out.append(AttachmentInput(**a))
        elif a.get("attachment_id"):
            out.append(AttachmentReference(attachment_id=a["attachment_id"]))
        else:
            raise ValueError(
                "attachment must have either 'data_base64' (new upload) or "
                "'attachment_id' (existing file reference)"
            )
    return out


def _patch_attachments(
    attachments: list[dict] | None | _UnsetType,
) -> list[AttachmentInput | AttachmentReference] | None | _UnsetType:
    """Apply patch semantics to the composer's ``attachments`` argument.

    ``UNSET`` (the caller omitted it) is passed through so
    ``gmail_update_draft`` preserves the draft's existing files - the composer's
    debounced autosave sends only text fields and must not strip attachments.
    ``None`` / ``[]`` still mean "clear". Anything else is coerced to models.
    """
    if isinstance(attachments, _UnsetType):
        return attachments
    return _coerce_attachments(attachments)


@mcp.tool(
    name="gmail_composer.save_draft",
    description="Persist the current composer fields onto an existing Gmail draft.",
    meta=_APP_META,
)
def save_draft(
    draft_id: str,
    user_id: str = "",
    to: str | None | _UnsetType = UNSET,
    subject: str | None | _UnsetType = UNSET,
    body: str | None | _UnsetType = UNSET,
    cc: str | None | _UnsetType = UNSET,
    bcc: str | None | _UnsetType = UNSET,
    attachments: list[dict] | None | _UnsetType = UNSET,
) -> GmailDraft:
    # Defaults are UNSET, not None: a field the composer omits must be preserved
    # (the patch contract), not cleared. None still means an explicit "clear".
    uid = guard_user_id(user_id)
    return _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=uid,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=_patch_attachments(attachments),
        )
    )


@mcp.tool(
    name="gmail_composer.send",
    description="Persist composer fields then send the draft via Gmail.",
    meta=_APP_META,
)
def send(
    draft_id: str,
    user_id: str = "",
    to: str | None | _UnsetType = UNSET,
    subject: str | None | _UnsetType = UNSET,
    body: str | None | _UnsetType = UNSET,
    cc: str | None | _UnsetType = UNSET,
    bcc: str | None | _UnsetType = UNSET,
    attachments: list[dict] | None | _UnsetType = UNSET,
) -> GmailSendResult:
    # UNSET defaults preserve omitted fields (see save_draft); the composer's
    # send path saves the visible fields then sends, leaving files intact.
    uid = guard_user_id(user_id)
    _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=uid,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=_patch_attachments(attachments),
        )
    )
    return _gmail_send(GmailSendInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.discard",
    description="Delete the current draft.",
    meta=_APP_META,
)
def discard(draft_id: str, user_id: str = "") -> GmailDiscardDraftResult:
    uid = guard_user_id(user_id)
    return _gmail_discard_draft(GmailDiscardDraftInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.refresh",
    description="Re-fetch the current draft (used by the composer to poll for agent edits).",
    meta=_APP_META,
)
def refresh(draft_id: str, user_id: str = "") -> GmailDraft:
    uid = guard_user_id(user_id)
    return _gmail_get_draft(GmailGetDraftInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.get_thread",
    description="Fetch the full thread for display in the composer's thread panel.",
    meta=_APP_META,
)
def get_thread(thread_id: str, user_id: str = "") -> GmailThread:
    uid = guard_user_id(user_id)
    # The composer's thread panel renders inline images, so it needs the full
    # bytes the model-facing gmail_get_thread omits by default.
    return _gmail_get_thread(
        GmailGetThreadInput(
            user_id=uid, thread_id=thread_id, include_attachment_data=True
        )
    )


@mcp.tool(
    name="gmail_composer.get_attachment",
    description="Fetch the raw base64 data for an attachment on a message.",
    meta=_APP_META,
)
def get_attachment(
    message_id: str, attachment_id: str, user_id: str = ""
) -> GmailAttachmentData:
    """Fetch an attachment's bytes for the composer's preview.

    Thin adapter over the canonical ``gmail_get_attachment`` service; the
    committed composer bundle reads ``data_base64`` off the structured content.
    """
    uid = guard_user_id(user_id)
    return _gmail_get_attachment(
        GmailGetAttachmentInput(
            user_id=uid, message_id=message_id, attachment_id=attachment_id
        )
    )
