"""MCP tool annotations (``readOnlyHint`` / ``destructiveHint`` / ``openWorldHint``).

Hosts use these hints to decide how to treat a tool: whether a confirmation is
needed, and whether the tool is offered at all on read-only plans or modes.
A tool with no annotations falls back to the spec defaults - not read-only and
destructive - so every DaySurface tool would look like a destructive write.

One table covers every tool, registry-backed and app-only alike, so the
classification is reviewable in one place. ``DaySurfaceMCP.tool`` in
``mcp_server/server.py`` applies it at registration, and
``tests/test_tool_annotations.py`` fails if a registered tool is missing.

Rules used for the three flags:

- ``read_only``: only fetches or lists; changes nothing in Gmail, the
  database, or anywhere else.
- ``destructive``: deletes something or does something that can't be taken
  back (deleting a draft, an attachment, a webhook, a signing secret or the
  Gmail link; sending an email). Additive or reversible changes (archive,
  mark read, create or edit a draft) are not.
- ``open_world``: can reach or affect people or systems outside the user's
  own account - sending email, posting webhooks, fetching arbitrary URLs.
"""

from typing import NamedTuple

from mcp.types import ToolAnnotations


class _Hints(NamedTuple):
    read_only: bool
    destructive: bool = False
    open_world: bool = False


_READ = _Hints(read_only=True)
_WRITE = _Hints(read_only=False)
_DELETE = _Hints(read_only=False, destructive=True)
_SEND = _Hints(read_only=False, destructive=True, open_world=True)

TOOL_HINTS: dict[str, _Hints] = {
    # Gmail: read
    "gmail_list_inbox": _READ,
    "gmail_get_thread": _READ,
    "gmail_get_attachment": _READ,
    "gmail_list_drafts": _READ,
    "gmail_get_draft": _READ,
    "gmail_curate_inbox": _READ,
    "gmail_get_focused_email": _READ,
    "gmail_status": _READ,
    "gmail_connect": _READ,  # only returns a link; the user completes the flow
    "inbox_search": _READ,
    "inbox_get_curation": _READ,
    # Gmail: reversible or additive writes
    "gmail_archive_thread": _WRITE,
    "gmail_mark_thread_read": _WRITE,
    "gmail_mark_thread_done": _WRITE,
    "gmail_unmark_thread_done": _WRITE,
    "gmail_compose": _WRITE,
    "gmail_update_draft": _WRITE,
    "gmail_reply_to_thread": _WRITE,
    "gmail_add_attachment": _WRITE,
    "gmail_watch_start": _WRITE,
    "gmail_watch_stop": _WRITE,
    "inbox_save_curation": _WRITE,
    # Gmail: deletes, and sending (the one tool that reaches other people)
    "gmail_discard_draft": _DELETE,
    "gmail_remove_attachment": _DELETE,
    "gmail_disconnect": _DELETE,
    "gmail_send": _SEND,
    # PDF forms
    "pdf_open": _WRITE,
    "pdf_edit": _WRITE,
    "pdf_request_signature": _WRITE,
    "pdf_export": _WRITE,
    # Account and settings
    "sign_out": _WRITE,  # revokes sessions; signing in again restores access
    "webhook_list": _READ,
    "webhook_settings": _READ,
    # App-only tools (called from inside MCP App iframes)
    "gmail_inbox.refresh": _READ,
    "gmail_inbox.open_thread": _READ,
    "gmail_inbox.fetch_image": _Hints(read_only=True, open_world=True),
    "gmail_inbox.mark_read": _WRITE,
    "gmail_inbox.archive": _WRITE,
    "gmail_inbox.mark_done": _WRITE,
    "gmail_inbox.unmark_done": _WRITE,
    "gmail_inbox.reply": _WRITE,
    "gmail_inbox.forward": _WRITE,
    "gmail_inbox.set_focus": _WRITE,
    "gmail_composer.refresh": _READ,
    "gmail_composer.get_thread": _READ,
    "gmail_composer.get_attachment": _READ,
    "gmail_composer.send_status": _READ,
    "gmail_composer.save_draft": _WRITE,
    "gmail_composer.add_attachment": _WRITE,
    "gmail_composer.remove_attachment": _DELETE,
    "gmail_composer.discard": _DELETE,
    "gmail_composer.send": _SEND,
    "pdf_signer.get_document": _READ,
    "pdf_signer.sign": _WRITE,
    "pdf_signer.cancel": _WRITE,
    "settings.get": _READ,
    "settings.subscribe": _Hints(read_only=False, open_world=True),
    "settings.unsubscribe": _DELETE,
    "settings.rotate_secret": _DELETE,  # the old secret stops working for good
}


def annotations_for(tool_name: str) -> ToolAnnotations | None:
    """Return the MCP annotations for ``tool_name``, or None if unclassified."""
    hints = TOOL_HINTS.get(tool_name)
    if hints is None:
        return None
    return ToolAnnotations(
        readOnlyHint=hints.read_only,
        destructiveHint=hints.destructive,
        openWorldHint=hints.open_world,
    )
