"""App-only tools used by the gmail_inbox MCP App.

These are visible to the iframe via ``visibility=["app"]`` but should not be
invoked by the LLM directly - the curated reader UI calls them on user action.
``user_id`` arrives on the wire but is overridden by the authenticated
principal when one is bound; see ``mcp_server/app_tools/_auth_guard.py``.

The ``set_focus`` / ``get_focused_email`` pair bridges the UI and LLM: the
iframe pushes focus state via an app-only tool, and the LLM reads it with a
model-visible tool so it can answer questions about the email the user is
currently viewing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mcp_server.app_tools._auth_guard import guard_user_id
from mcp_server.server import mcp
from models.gmail import (
    GmailComposeInput,
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailDraft,
    GmailFetchImageResult,
    GmailGetThreadInput,
    GmailThread,
)
from services.gmail_curate_svc import (
    gmail_curate_inbox as _gmail_curate_inbox,
)
from services.gmail_drafts_svc import (
    GmailReplyInput,
)
from services.gmail_drafts_svc import (
    gmail_compose as _gmail_compose,
)
from services.gmail_drafts_svc import (
    gmail_reply_to_thread as _gmail_reply_to_thread,
)
from services.gmail_messages_svc import (
    GmailArchiveResult,
    GmailMarkDoneResult,
    GmailMarkReadResult,
    GmailThreadModifyInput,
    GmailUnmarkDoneResult,
)
from services.gmail_messages_svc import (
    gmail_archive_thread as _gmail_archive_thread,
)
from services.gmail_messages_svc import (
    gmail_get_thread as _gmail_get_thread,
)
from services.gmail_messages_svc import (
    gmail_mark_thread_done as _gmail_mark_thread_done,
)
from services.gmail_messages_svc import (
    gmail_mark_thread_read as _gmail_mark_thread_read,
)
from services.gmail_messages_svc import (
    gmail_unmark_thread_done as _gmail_unmark_thread_done,
)
from services.image_proxy import (
    fetch_remote_image as _fetch_remote_image,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="gmail_inbox.refresh",
    description="Re-run gmail_curate_inbox (called by the inbox reader app).",
    meta=_APP_META,
)
def refresh(
    user_id: str = "",
    query: str | None = None,
    limit: int = 10,
) -> GmailCurateInboxResult:
    uid = guard_user_id(user_id)
    return _gmail_curate_inbox(
        GmailCurateInboxInput(user_id=uid, query=query, limit=limit)
    )


@mcp.tool(
    name="gmail_inbox.open_thread",
    description="Fetch a single thread for the inbox reader app.",
    meta=_APP_META,
)
def open_thread(thread_id: str, user_id: str = "") -> GmailThread:
    uid = guard_user_id(user_id)
    # The reader iframe renders inline images and attachment previews, so it
    # needs the full bytes the model-facing gmail_get_thread omits by default.
    return _gmail_get_thread(
        GmailGetThreadInput(
            user_id=uid, thread_id=thread_id, include_attachment_data=True
        )
    )


@mcp.tool(
    name="gmail_inbox.mark_read",
    description="Mark a thread as read (removes the UNREAD label).",
    meta=_APP_META,
)
def mark_read(thread_id: str, user_id: str = "") -> GmailMarkReadResult:
    uid = guard_user_id(user_id)
    return _gmail_mark_thread_read(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.archive",
    description="Archive a thread (removes the INBOX label).",
    meta=_APP_META,
)
def archive(thread_id: str, user_id: str = "") -> GmailArchiveResult:
    uid = guard_user_id(user_id)
    return _gmail_archive_thread(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.reply",
    description="Create a reply draft on a thread (the composer app opens it next).",
    meta=_APP_META,
)
def reply(
    thread_id: str,
    user_id: str = "",
    body: str | None = None,
    subject: str | None = None,
) -> GmailDraft:
    uid = guard_user_id(user_id)
    return _gmail_reply_to_thread(
        GmailReplyInput(user_id=uid, thread_id=thread_id, body=body, subject=subject)
    )


@mcp.tool(
    name="gmail_inbox.forward",
    description="Create a forward draft for a message in a thread.",
    meta=_APP_META,
)
def forward(
    thread_id: str,
    subject: str = "",
    body: str = "",
    user_id: str = "",
) -> GmailDraft:
    uid = guard_user_id(user_id)
    fwd_subject = subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}"
    return _gmail_compose(
        GmailComposeInput(
            user_id=uid,
            to="",
            subject=fwd_subject,
            body=body,
            in_reply_to_thread_id=thread_id,
        )
    )


@mcp.tool(
    name="gmail_inbox.mark_done",
    description="Mark a thread as done (applies MCP/Done label, hides from curated inbox).",
    meta=_APP_META,
)
def mark_done(thread_id: str, user_id: str = "") -> GmailMarkDoneResult:
    uid = guard_user_id(user_id)
    return _gmail_mark_thread_done(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.unmark_done",
    description="Remove the done marker from a thread (undo mark-done).",
    meta=_APP_META,
)
def unmark_done(thread_id: str, user_id: str = "") -> GmailUnmarkDoneResult:
    uid = guard_user_id(user_id)
    return _gmail_unmark_thread_done(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


# ---------------------------------------------------------------------------
# Focus state: bridges the iframe UI ↔ LLM
# ---------------------------------------------------------------------------

_focused_threads: dict[str, dict[str, Any]] = {}


class _SetFocusResult(BaseModel):
    ok: bool = True


class _FocusedEmailResult(BaseModel):
    focused: bool
    thread_id: str | None = None
    subject: str | None = None
    from_: str | None = None
    message_count: int = 0
    messages: list[dict[str, Any]] | None = None


@mcp.tool(
    name="gmail_inbox.set_focus",
    description="Store which thread the user is currently viewing (called by inbox UI).",
    meta=_APP_META,
)
def set_focus(
    thread_id: str | None = None,
    subject: str | None = None,
    from_: str | None = None,
    message_count: int = 0,
    messages: list[dict[str, Any]] | None = None,
    user_id: str = "",
) -> _SetFocusResult:
    uid = guard_user_id(user_id)
    if thread_id is None:
        _focused_threads.pop(uid, None)
    else:
        _focused_threads[uid] = {
            "thread_id": thread_id,
            "subject": subject,
            "from": from_,
            "message_count": message_count,
            "messages": messages,
        }
    return _SetFocusResult()


@mcp.tool(
    name="gmail_get_focused_email",
    description="Return the email thread the user is currently viewing in the inbox UI. Call this when the user asks about 'this email', 'the email I'm looking at', or references the currently open thread.",
)
def get_focused_email(user_id: str = "") -> _FocusedEmailResult:
    uid = guard_user_id(user_id)
    data = _focused_threads.get(uid)
    if not data:
        return _FocusedEmailResult(focused=False)
    return _FocusedEmailResult(
        focused=True,
        thread_id=data.get("thread_id"),
        subject=data.get("subject"),
        from_=data.get("from"),
        message_count=data.get("message_count", 0),
        messages=data.get("messages"),
    )


@mcp.tool(
    name="gmail_inbox.fetch_image",
    description=(
        "Fetch a remote image referenced by an email's HTML and return it "
        "as base64 (called by the inbox reader app when the host CSP blocks "
        "remote img-src)."
    ),
    meta=_APP_META,
)
def fetch_image(url: str, user_id: str = "") -> GmailFetchImageResult:
    guard_user_id(user_id)
    return _fetch_remote_image(url)
