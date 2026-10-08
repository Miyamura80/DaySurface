"""Contracts for the gmail_composer MCP App's app-only tools."""

from pydantic import BaseModel


class GmailComposerSendStatus(BaseModel):
    """Whether a draft still exists, so the composer can resolve a lost send reply."""

    draft_exists: bool
