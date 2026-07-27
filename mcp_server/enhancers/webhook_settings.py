"""Settings enhancer - renders the Settings MCP App when the client supports it.

``webhook_settings`` returns the snapshot headlessly for non-App clients; when
the host can show apps, the iframe (``ui://mymcp/settings``) is attached and
receives that same snapshot as its initial ``ontoolresult`` payload.
"""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.webhook_settings import WebhookSettingsInput, WebhookSettingsResult

APP_URI = "ui://mymcp/settings"


@enhance("webhook_settings", fallback="headless", app_uri=APP_URI)
async def webhook_settings_enhanced(
    tool: EnhancedTool[WebhookSettingsInput, WebhookSettingsResult],
) -> WebhookSettingsResult:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result
