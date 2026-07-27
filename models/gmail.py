"""Pydantic input/output schemas for the Gmail integration.

Every input model carries ``user_id`` explicitly - the MCP tool factory
and FastAPI dependencies inject it from the authenticated principal in
a later wiring step (we deliberately do not use ContextVars).
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field

# The "omitted vs explicit-null" sentinel lives in ``models.patch`` (it is
# transport-generic, not Gmail-specific). Re-exported here so callers can keep
# importing ``UNSET`` / ``unset_to`` from the Gmail models facade.
from models.patch import (
    UNSET as UNSET,
)
from models.patch import (
    _PatchStr,
    _UnsetJson,
    _UnsetType,
)
from models.patch import (
    unset_to as unset_to,
)

# ---------------------------------------------------------------------------
# Connect / status / disconnect
# ---------------------------------------------------------------------------


class GmailConnectInput(BaseModel):
    user_id: str = Field(default="", description="The MCP user id starting the flow")


class GmailConnectResult(BaseModel):
    auth_url: str
    state: str


class GmailStatusInput(BaseModel):
    user_id: str = ""


class GmailStatusResult(BaseModel):
    connected: bool
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    granted_at: datetime | None = None


class GmailDisconnectInput(BaseModel):
    user_id: str = ""


class GmailDisconnectResult(BaseModel):
    revoked: bool


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


class AttachmentInput(BaseModel):
    """A file attachment to include on an outgoing email.

    Pass ``data_base64`` with the file content base64-encoded. Works from any
    MCP host (ChatGPT, Claude, etc.) regardless of filesystem access.
    """

    filename: str = Field(
        description="Display name for the attachment, e.g. 'report.pdf'",
        min_length=1,
        max_length=256,
    )
    mime_type: str = Field(
        description="MIME type, e.g. 'application/pdf'",
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*\/[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*$",
    )
    # Gmail's send limit is 25 MB; 34 MB base64 ≈ 25.5 MB decoded.
    data_base64: str = Field(
        description="Base64-encoded file content",
        min_length=1,
        max_length=34_000_000,
    )


class AttachmentUpload(BaseModel):
    """A normalized attachment payload ready to write into an outgoing MIME message.

    This is the single shape ``_build_raw_message`` consumes. It deliberately
    skips ``AttachmentInput``'s strict validators (mime pattern, size caps) so
    bytes re-downloaded from an existing draft - already accepted by Gmail once -
    never fail re-validation on a preserve path.
    """

    filename: str
    mime_type: str
    data_base64: str


class InlineImageUpload(BaseModel):
    """A CID-referenced inline image to re-emit when rebuilding an HTML body.

    HTML draft bodies reference inline images by ``cid:<content_id>``. On a
    whole-message rebuild these parts must be re-attached (as multipart/related
    with their ``Content-ID``) or the HTML renders with broken images.
    ``data_base64`` is base64url, matching ``AttachmentUpload``.
    """

    content_id: str
    mime_type: str
    data_base64: str


class AttachmentReference(BaseModel):
    """Reference to an attachment already present on a draft, by its stable id.

    Pass this (instead of an ``AttachmentInput``) in ``gmail_update_draft`` to
    keep an existing file on the draft without re-uploading its bytes. The
    ``attachment_id`` comes from the ``attachments[].attachment_id`` echoed by
    any prior draft mutation (compose / update / add / remove) or
    ``gmail_get_draft``.
    """

    attachment_id: str = Field(
        description="Stable id of an attachment already on the draft to preserve",
        min_length=1,
    )


class GmailListDraftsInput(BaseModel):
    user_id: str = ""
    limit: int = Field(default=20, ge=1, le=500)


class GmailDraftSummary(BaseModel):
    draft_id: str
    to: str | None = None
    subject: str | None = None
    snippet: str | None = None
    updated_at: datetime | None = None


class GmailListDraftsResult(BaseModel):
    drafts: list[GmailDraftSummary]


class GmailGetDraftInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailDraftAttachment(BaseModel):
    """Metadata for an attachment already on a draft (read-only, no data blob).

    ``attachment_id`` is the stable handle for the file: pass it back as an
    ``AttachmentReference`` to ``gmail_update_draft`` (or to
    ``gmail_remove_attachment``) to preserve / remove the file without
    re-uploading its bytes.
    """

    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None
    message_id: str | None = None

    @computed_field
    @property
    def size_bytes(self) -> int | None:
        """Size in bytes - the name the public tool contract advertises.

        Emitted alongside ``size`` (not instead of it) because the committed
        composer UI bundle still reads ``size``; collapsing to a single key
        would require rebuilding that React bundle (``make build_apps``), which
        is out of scope for a pure service change.
        """
        return self.size


class GmailDraft(BaseModel):
    draft_id: str
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str | None = None
    thread_id: str | None = None
    attachments: list[GmailDraftAttachment] = Field(default_factory=list)

    @computed_field
    @property
    def body_preview(self) -> str | None:
        """First ~200 chars of the body, so callers can verify content cheaply."""
        if self.body is None:
            return None
        return self.body[:200]


class GmailUpdateDraftInput(BaseModel):
    """Patch input for ``gmail_update_draft``.

    Non-destructive by default: a field you omit is left unchanged on the
    draft; a field you set to ``null`` is cleared. This applies to ``to``,
    ``cc``, ``bcc``, ``subject``, ``body``, and ``attachments``.

    The omitted-vs-null distinction is carried by the ``UNSET`` sentinel
    default rather than ``model_fields_set``, because the latter cannot tell
    the two apart over the MCP transport (see ``_UnsetType``). Every patchable
    field therefore defaults to ``UNSET`` (preserve) instead of ``None``
    (clear).

    ``attachments`` accepts a mix of new uploads (``AttachmentInput`` with
    base64 bytes) and references to files already on the draft
    (``AttachmentReference`` with just an ``attachment_id``), so a caller can
    edit the body repeatedly without re-uploading attachments. Omit
    ``attachments`` to keep every existing file; pass ``null`` (or ``[]``) to
    drop them all.
    """

    # UNSET is not a Pydantic type, so allow it as a field default/value.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str = ""
    draft_id: str
    to: _PatchStr = UNSET
    subject: _PatchStr = UNSET
    body: _PatchStr = UNSET
    cc: _PatchStr = UNSET
    bcc: _PatchStr = UNSET
    attachments: Annotated[
        list[AttachmentInput | AttachmentReference] | None | _UnsetType, _UnsetJson
    ] = UNSET


class GmailAddAttachmentInput(BaseModel):
    """Input for ``gmail_add_attachment``: append one file to a draft.

    Touches only the attachment list - body, subject, and recipients are
    preserved verbatim.
    """

    user_id: str = ""
    draft_id: str
    attachment: AttachmentInput


class GmailRemoveAttachmentInput(BaseModel):
    """Input for ``gmail_remove_attachment``: drop one file from a draft by id.

    Touches only the attachment list - body, subject, and recipients are
    preserved verbatim.
    """

    user_id: str = ""
    draft_id: str
    attachment_id: str = Field(
        description="Stable id of the attachment to remove", min_length=1
    )


class GmailDraftAttachmentsResult(BaseModel):
    """The draft's attachment list after an add/remove operation."""

    draft_id: str
    attachments: list[GmailDraftAttachment] = Field(default_factory=list)


class GmailComposeInput(BaseModel):
    user_id: str = ""
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None
    in_reply_to_thread_id: str | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list)


class GmailReplyInput(BaseModel):
    """Input for ``gmail_reply_to_thread``: create a reply draft on a thread.

    ``body`` defaults to an empty placeholder so the composer UI can populate
    it on the next turn. ``subject`` defaults to ``Re: <orig>`` derived from
    the thread's last message.

    Recipients are caller-controlled: ``to``, ``cc``, and ``bcc`` each accept a
    comma-separated address list and are used verbatim when provided. Only
    ``to`` has a default - when omitted it is derived from the thread (the other
    party in the conversation, never the account owner). ``cc``/``bcc`` are set
    only when the caller passes them; the reply carries none otherwise. Supplied
    addresses are used as-is - they are NOT de-duplicated against the thread's
    existing participants or the derived ``to`` default.
    """

    user_id: str = ""
    thread_id: str
    body: str | None = None
    subject: str | None = None
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list)


class GmailSendInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailSendResult(BaseModel):
    message_id: str
    thread_id: str | None = None
    sent_at: datetime


class GmailDiscardDraftInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailDiscardDraftResult(BaseModel):
    discarded: bool


# ---------------------------------------------------------------------------
# Inbox / threads
# ---------------------------------------------------------------------------


class GmailListInboxInput(BaseModel):
    user_id: str = ""
    query: str | None = None
    limit: int = Field(default=25, ge=1, le=500)


class GmailMessageSummary(BaseModel):
    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    date: datetime | None = None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailListInboxResult(BaseModel):
    messages: list[GmailMessageSummary]


class GmailGetThreadInput(BaseModel):
    user_id: str = ""
    thread_id: str
    include_attachment_data: bool = Field(
        default=False,
        description=(
            "Inline the raw base64 bytes of attachments and cid: inline images "
            "(e.g. signature logos) into the response. Off by default to keep "
            "thread payloads small - attachments still carry filename, mime_type, "
            "size, and attachment_id, so fetch a file's bytes on demand with "
            "gmail_get_attachment using its message_id + attachment_id."
        ),
    )
    strip_quoted_replies: bool = Field(
        default=False,
        description=(
            "Collapse quoted prior-message history from each reply's body, "
            "keeping only the newly written text. Off by default; turn on to "
            "read long threads cheaply without every message re-quoting the whole "
            "chain."
        ),
    )


class GmailAttachment(BaseModel):
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None
    content_id: str | None = None
    data: str | None = None


class GmailGetAttachmentInput(BaseModel):
    user_id: str = ""
    message_id: str = Field(
        description="Id of the message the attachment lives on (from gmail_get_thread)",
        min_length=1,
    )
    attachment_id: str = Field(
        description="Stable attachment id from a gmail_get_thread attachment entry",
        min_length=1,
    )


class GmailAttachmentData(BaseModel):
    """Raw bytes of a single attachment, fetched on demand.

    ``data_base64`` is standard base64 (padded), ready to decode or re-upload.
    """

    message_id: str
    attachment_id: str
    size: int | None = None
    data_base64: str


class GmailThreadMessage(BaseModel):
    message_id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    cc: str | None = None
    date: datetime | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[GmailAttachment] = Field(default_factory=list)

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailThread(BaseModel):
    thread_id: str
    messages: list[GmailThreadMessage]
    draft: GmailDraft | None = None


# ---------------------------------------------------------------------------
# Curated inbox (deterministic heuristics for v1; DSPY ranker comes later)
# ---------------------------------------------------------------------------


class GmailCurateInboxInput(BaseModel):
    user_id: str = ""
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class GmailLabelChip(BaseModel):
    name: str
    bg_color: str = "#f1f3f4"
    text_color: str = "#444444"


class GmailCuratedThread(BaseModel):
    thread_id: str
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    last_message_at: datetime | None = None
    importance_score: float
    reasons: list[str] = Field(default_factory=list)
    labels: list[GmailLabelChip] = Field(default_factory=list)
    has_draft: bool = False
    draft_id: str | None = None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailCurateInboxResult(BaseModel):
    threads: list[GmailCuratedThread]


# ---------------------------------------------------------------------------
# Remote image proxy (inbox reader app)
# ---------------------------------------------------------------------------


class GmailFetchImageResult(BaseModel):
    """A remote email image fetched server-side and returned as base64.

    Strict-CSP MCP hosts (e.g. claude.ai) block ``img-src`` for arbitrary
    remote origins inside the app iframe, so the reader app asks the server
    to fetch the bytes and renders them as a ``data:`` URI instead.
    """

    url: str
    mime_type: str
    data_base64: str
