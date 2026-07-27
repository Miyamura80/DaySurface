"""Pydantic schemas for Gmail watch (users.watch) management.

A watch subscribes a mailbox to Gmail push notifications delivered via the
configured Pub/Sub topic. Watches expire after ~7 days and are renewed by the
periodic runner; these models back the start/stop services.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class GmailWatchStartInput(BaseModel):
    user_id: str = Field(default="", description="User whose mailbox to watch")


class GmailWatchStartResult(BaseModel):
    watching: bool
    history_id: str | None = None
    expiration: datetime | None = None


class GmailWatchStopInput(BaseModel):
    user_id: str = ""


class GmailWatchStopResult(BaseModel):
    stopped: bool
