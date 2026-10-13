"""Contracts for account-level operations."""

from pydantic import BaseModel, Field


class SignOutInput(BaseModel):
    user_id: str = ""


class SignOutResult(BaseModel):
    sessions_revoked: int = Field(
        description="Number of active sessions ended. Every client signed in "
        "under them must sign in again.",
    )
