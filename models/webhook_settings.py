"""Pydantic schemas for the Settings MCP App snapshot.

``webhook_settings`` returns everything the Settings panel needs to render in
one call: the user's Gmail connection + watch status and their webhook
subscriptions (never the signing secrets, which are shown only once at
create/rotate time).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from models.webhooks import WebhookSubscriptionView


class WebhookSettingsInput(BaseModel):
    user_id: str = Field(default="", description="Owner of the settings snapshot")


class WebhookSettingsResult(BaseModel):
    # Gmail connection / watch
    gmail_connected: bool = False
    gmail_email: str | None = None
    watching: bool = False
    watch_expiration: datetime | None = None
    # Whether the operator has configured the Pub/Sub push pipeline at all.
    # When false, the panel explains that email webhooks are unavailable.
    push_available: bool = False
    # Existing subscriptions (secrets never included).
    subscriptions: list[WebhookSubscriptionView] = Field(default_factory=list)
