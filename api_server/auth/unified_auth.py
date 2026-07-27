"""Unified authentication - tries Bearer JWT first, then API key, then 401."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api_server.auth.api_key_auth import validate_api_key
from api_server.auth.workos_auth import verify_workos_token
from common import global_config
from db.engine import get_db_session


@dataclass
class AuthenticatedUser:
    user_id: str
    email: str | None = None
    auth_method: str = "jwt"  # "jwt" | "api_key"
    scopes: list[str] | None = None  # None = legacy (no restrictions)


def get_authenticated_user(
    request: Request,
    session: Session = Depends(get_db_session),
) -> AuthenticatedUser:
    """FastAPI dependency that authenticates via JWT or API key."""
    # 1. Try Bearer JWT (fail fast only when WorkOS is configured)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        workos_user = verify_workos_token(token)
        if workos_user:
            return AuthenticatedUser(
                user_id=workos_user.user_id,
                email=workos_user.email,
                auth_method="jwt",
                # JWT users get full access -- require_scopes() is a
                # no-op for them.  This is intentional: JWT auth is for
                # first-party users via the dashboard, while API keys
                # serve third-party integrations with granular scopes.
                # If JWT auth is extended to less-trusted users, this
                # should be replaced with role-based scope assignment.
                scopes=["*"],
            )
        # Only fail fast if WorkOS is actually configured; otherwise
        # the Bearer header may be irrelevant and API key should be tried.
        if global_config.WORKOS_CLIENT_ID:
            raise HTTPException(status_code=401, detail="Invalid Bearer token")

    # 2. Try API key (header)
    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        row = validate_api_key(session, api_key)
        if row:
            return AuthenticatedUser(
                user_id=row.user_id,
                auth_method="api_key",
                scopes=row.scopes,
            )

    raise HTTPException(status_code=401, detail="Authentication required")
