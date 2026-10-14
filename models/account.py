"""Contracts for account-level operations."""

from pydantic import BaseModel, Field


class SignOutInput(BaseModel):
    user_id: str = ""


class SignOutResult(BaseModel):
    tokens_revoked: int = Field(
        description="DaySurface access and refresh tokens revoked. Every app "
        "that held one must sign in again.",
    )
    sign_in_sessions_ended: int = Field(
        description="WorkOS sign-in sessions ended, so signing back in needs a "
        "fresh login rather than a silent one.",
    )
