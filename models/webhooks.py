"""Pydantic input/output schemas for outbound webhook management.

These back the ``webhook_*`` services (subscribe / list / unsubscribe /
rotate-secret). Like the Gmail models, every input carries ``user_id``
explicitly; the transport layer injects it from the authenticated
principal. The signing ``secret`` is returned only at creation / rotation
time and is never echoed back by list.
"""

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


class WebhookSubscribeInput(BaseModel):
    user_id: str = Field(default="", description="Owner of the subscription")
    url: str = Field(
        description="HTTPS endpoint that will receive signed webhook POSTs",
        min_length=1,
        max_length=2048,
    )
    event_types: list[str] | None = Field(
        default=None,
        description="Event types to receive; omit/empty means all event types",
    )


class WebhookSubscribeResult(BaseModel):
    id: str
    url: str
    event_types: list[str] | None = None
    active: bool = True
    # Shown once. Store it - it is required to verify the X-Webhook-Signature
    # HMAC on delivered payloads and cannot be retrieved again.
    secret: str = Field(description="HMAC signing secret; shown only once")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class WebhookSubscriptionView(BaseModel):
    id: str
    url: str
    event_types: list[str] | None = None
    active: bool
    created_at: datetime | None = None


class WebhookListInput(BaseModel):
    user_id: str = ""


class WebhookListResult(BaseModel):
    subscriptions: list[WebhookSubscriptionView] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Unsubscribe / rotate
# ---------------------------------------------------------------------------


class WebhookUnsubscribeInput(BaseModel):
    user_id: str = ""
    subscription_id: str


class WebhookUnsubscribeResult(BaseModel):
    unsubscribed: bool


class WebhookRotateSecretInput(BaseModel):
    user_id: str = ""
    subscription_id: str


class WebhookRotateSecretResult(BaseModel):
    id: str
    secret: str = Field(description="New HMAC signing secret; shown only once")
