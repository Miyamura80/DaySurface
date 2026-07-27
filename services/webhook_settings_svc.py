"""Settings snapshot service - Gmail/watch status + webhook subscriptions.

Read-only aggregate that backs the Settings MCP App's initial render. Pure and
transport-agnostic like every other ``@service``; the MCP enhancer attaches the
iframe on top of it.
"""

from __future__ import annotations

from common import global_config
from db.engine import use_db_session
from db.models.google_tokens import GoogleToken
from db.models.webhooks import WebhookSubscription
from models.webhook_settings import WebhookSettingsInput, WebhookSettingsResult
from models.webhooks import WebhookSubscriptionView
from services import service


@service(
    name="webhook_settings",
    description="Open your settings: Gmail connection status and webhook subscriptions",
    input_model=WebhookSettingsInput,
    output_model=WebhookSettingsResult,
)
def webhook_settings(input: WebhookSettingsInput) -> WebhookSettingsResult:
    with use_db_session() as session:
        token = (
            session.query(GoogleToken)
            .filter(
                GoogleToken.user_id == input.user_id,
                GoogleToken.revoked_at.is_(None),
            )
            .one_or_none()
        )
        subs = (
            session.query(WebhookSubscription)
            .filter(WebhookSubscription.user_id == input.user_id)
            .order_by(WebhookSubscription.created_at.desc())
            .all()
        )
        views = [
            WebhookSubscriptionView(
                id=s.id,
                url=s.url,
                event_types=s.event_types,
                active=s.active,
                created_at=s.created_at,
            )
            for s in subs
        ]

    return WebhookSettingsResult(
        gmail_connected=token is not None,
        gmail_email=token.email if token else None,
        watching=bool(token and token.watch_history_id),
        watch_expiration=token.watch_expiration if token else None,
        push_available=bool(global_config.GMAIL_PUBSUB_TOPIC),
        subscriptions=views,
    )
