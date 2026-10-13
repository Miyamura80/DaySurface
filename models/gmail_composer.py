"""Contracts for the gmail_composer MCP App's app-only tools."""

from pydantic import BaseModel

from models.gmail import GmailSendResult


class GmailComposerSendStatus(BaseModel):
    """What became of a draft, so the composer can resolve a lost send reply.

    ``sent`` is set when this server sent the draft. A draft that is gone with
    no ``sent`` record was discarded, or sent or deleted outside this server.
    """

    draft_exists: bool
    sent: GmailSendResult | None = None
