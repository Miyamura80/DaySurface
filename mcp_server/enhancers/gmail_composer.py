"""Gmail composer enhancers - route draft results to the inbox app's InlineComposer.

All draft-producing tools (``gmail_update_draft``, ``gmail_reply_to_thread``,
and ``gmail_compose``) call ``send_app(INBOX_URI)`` so the host delivers the
tool result to the inbox app. Its ``ontoolresult`` handler detects the
``draft_id`` in the payload and activates the InlineComposer view.

The headless service result is returned unchanged so non-UI clients see
identical output.
"""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.gmail import (
    GmailComposeInput,
    GmailDraft,
    GmailUpdateDraftInput,
)
from services.gmail_drafts_svc import GmailReplyInput

INBOX_URI = "ui://mymcp/gmail_inbox"


@enhance("gmail_compose", fallback="headless", app_uri=INBOX_URI)
async def gmail_compose_enhanced(
    tool: EnhancedTool[GmailComposeInput, GmailDraft],
) -> GmailDraft:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(INBOX_URI)
    return result


@enhance("gmail_update_draft", fallback="headless", app_uri=INBOX_URI)
async def gmail_update_draft_enhanced(
    tool: EnhancedTool[GmailUpdateDraftInput, GmailDraft],
) -> GmailDraft:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(INBOX_URI)
    return result


@enhance("gmail_reply_to_thread", fallback="headless", app_uri=INBOX_URI)
async def gmail_reply_to_thread_enhanced(
    tool: EnhancedTool[GmailReplyInput, GmailDraft],
) -> GmailDraft:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(INBOX_URI)
    return result
