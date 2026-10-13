"""Contracts for the gmail_composer MCP App's app-only tools."""

from typing import Literal, Self

from pydantic import BaseModel, model_validator

from models.gmail import GmailSendResult


class GmailComposerSendStatus(BaseModel):
    """What became of a draft, so the composer can resolve a lost send reply.

    ``sent``: this server sent it, and ``result`` holds that send. ``unsent``:
    the draft still exists. ``gone``: the draft is gone with no send on record,
    so it was discarded, or sent or deleted outside this server.
    """

    state: Literal["sent", "unsent", "gone"]
    result: GmailSendResult | None = None

    @model_validator(mode="after")
    def _result_iff_sent(self) -> Self:
        if (self.state == "sent") != (self.result is not None):
            raise ValueError("result is set exactly when state is 'sent'")
        return self
