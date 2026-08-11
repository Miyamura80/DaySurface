"""App-only tools used by the Settings MCP App.

Visible to the iframe (``visibility=["app"]``) but hidden from the LLM. Each
call's ``user_id`` arrives on the wire but is overridden by the authenticated
principal when one is bound - see ``mcp_server/app_tools/_auth_guard.py`` - so
the panel can only ever read or mutate the caller's own settings.
"""

from __future__ import annotations

from mcp_server.app_tools._auth_guard import guard_user_id
from mcp_server.server import mcp
from models.webhook_settings import WebhookSettingsInput, WebhookSettingsResult
from models.webhooks import (
    WebhookRotateSecretInput,
    WebhookRotateSecretResult,
    WebhookSubscribeInput,
    WebhookSubscribeResult,
    WebhookUnsubscribeInput,
    WebhookUnsubscribeResult,
)
from services.webhook_settings_svc import webhook_settings as _webhook_settings
from services.webhooks_svc import (
    webhook_rotate_secret as _webhook_rotate_secret,
)
from services.webhooks_svc import (
    webhook_subscribe as _webhook_subscribe,
)
from services.webhooks_svc import (
    webhook_unsubscribe as _webhook_unsubscribe,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="settings.get",
    description="Fetch the current settings snapshot for the Settings app.",
    meta=_APP_META,
)
def get(user_id: str = "") -> WebhookSettingsResult:
    uid = guard_user_id(user_id)
    return _webhook_settings(WebhookSettingsInput(user_id=uid))


# SECURITY (audit #3): the headless `webhook_subscribe` service is hard-removed
# from the LLM surface (`hide_from_llm=True`), but this app-tool twin calls the
# same logic and is only kept from the model by the `visibility=["app"]` hint -
# which is advisory, not a server-side guarantee (see app_tools/__init__.py). On
# a host that ignores the hint, an injected email could still drive
# `settings.subscribe` to exfiltrate mail. Closing that fully requires serving
# app-only tools from a separate FastMCP instance (out of scope here); until
# then this path's safety is host-dependent, unlike the headless service.
@mcp.tool(
    name="settings.subscribe",
    description="Register a webhook endpoint (returns the one-time signing secret).",
    meta=_APP_META,
)
def subscribe(
    url: str,
    event_types: list[str] | None = None,
    user_id: str = "",
) -> WebhookSubscribeResult:
    uid = guard_user_id(user_id)
    return _webhook_subscribe(
        WebhookSubscribeInput(user_id=uid, url=url, event_types=event_types)
    )


@mcp.tool(
    name="settings.rotate_secret",
    description="Issue a new signing secret for a webhook subscription.",
    meta=_APP_META,
)
def rotate_secret(subscription_id: str, user_id: str = "") -> WebhookRotateSecretResult:
    uid = guard_user_id(user_id)
    return _webhook_rotate_secret(
        WebhookRotateSecretInput(user_id=uid, subscription_id=subscription_id)
    )


@mcp.tool(
    name="settings.unsubscribe",
    description="Deactivate a webhook subscription.",
    meta=_APP_META,
)
def unsubscribe(subscription_id: str, user_id: str = "") -> WebhookUnsubscribeResult:
    uid = guard_user_id(user_id)
    return _webhook_unsubscribe(
        WebhookUnsubscribeInput(user_id=uid, subscription_id=subscription_id)
    )
