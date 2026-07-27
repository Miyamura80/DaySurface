"""Gmail inbox enhancer - attaches the curated-inbox MCP App when the client supports it.

Both ``gmail_curate_inbox`` and ``gmail_get_thread`` route to the same inbox
app. The app's ``ontoolresult`` handler inspects the payload shape to decide
which view to render: thread list (curated inbox) or single thread reader.
"""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.curation import GetCurationInput, GetCurationResult
from models.gmail import (
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailGetThreadInput,
    GmailThread,
)

APP_URI = "ui://mymcp/gmail_inbox"


@enhance("gmail_curate_inbox", fallback="headless", app_uri=APP_URI)
async def gmail_curate_inbox_enhanced(
    tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult],
) -> GmailCurateInboxResult:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result


@enhance("inbox_get_curation", fallback="headless", app_uri=APP_URI)
async def inbox_get_curation_enhanced(
    tool: EnhancedTool[GetCurationInput, GetCurationResult],
) -> GetCurationResult:
    """Render the inbox dashboard from banked ledger verdicts + coverage.

    The app's ``ontoolresult`` handler detects the ``records``/``coverage``
    payload shape and renders the persisted curation (with a coverage banner),
    so the dashboard is consistent across sessions and clients.
    """
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result


@enhance("gmail_get_thread", fallback="headless", app_uri=APP_URI)
async def gmail_get_thread_enhanced(
    tool: EnhancedTool[GmailGetThreadInput, GmailThread],
) -> GmailThread:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result
