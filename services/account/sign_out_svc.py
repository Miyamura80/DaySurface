"""Sign out of DaySurface everywhere.

With ``MCP_AUTH_MODE=self``, DaySurface's own OAuth server (``api_server/oauth/``)
issues every token an MCP client holds and stores it in ``oauth_tokens``. Signing
out revokes all of the user's live tokens there, so each app's next request is
refused (401) and its refresh token no longer works. Codes issued but not yet
exchanged are spent, and the sign-out time is recorded so a sign-in already
under way cannot finish. It then ends the WorkOS sign-in sessions those tokens
came from, so signing back in needs a real login instead of a silent one.

In ``MCP_AUTH_MODE=authkit`` the tokens are WorkOS AuthKit's and DaySurface cannot
revoke them, so the service refuses rather than reporting a sign-out that did
not happen.
"""

from datetime import UTC, datetime

import httpx
from loguru import logger as log
from sqlalchemy import select, update

from common import global_config
from db.engine import use_db_session
from db.models.oauth import OAuthCode, OAuthSignOut, OAuthToken, lock_user
from models.account import SignOutInput, SignOutResult
from services import ClientRefusalError, service

WORKOS_REVOKE_SESSION_URL = "https://api.workos.com/user_management/sessions/revoke"
_TIMEOUT_SECONDS = 10.0


def _revoke_tokens(user_id: str) -> tuple[int, list[str]]:
    """Revoke the user's live tokens and codes; return the count and WorkOS sessions."""
    with use_db_session() as session:
        lock_user(session, user_id)
        now = datetime.now(UTC)
        token_sessions = session.scalars(
            select(OAuthToken.workos_session_id).where(
                OAuthToken.user_id == user_id,
                OAuthToken.revoked_at.is_(None),
                OAuthToken.workos_session_id.is_not(None),
            )
        )
        # A code not yet exchanged carries the WorkOS session of its sign-in.
        code_sessions = session.scalars(
            select(OAuthCode.workos_session_id).where(
                OAuthCode.user_id == user_id,
                OAuthCode.used_at.is_(None),
                OAuthCode.workos_session_id.is_not(None),
            )
        )
        session_ids = sorted({sid for sid in [*token_sessions, *code_sessions] if sid})
        result = session.execute(
            update(OAuthToken)
            .where(OAuthToken.user_id == user_id, OAuthToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        session.execute(
            update(OAuthCode)
            .where(OAuthCode.user_id == user_id, OAuthCode.used_at.is_(None))
            .values(used_at=now)
        )
        # Checked by the code exchange and the WorkOS callback (see OAuthSignOut).
        session.merge(OAuthSignOut(user_id=user_id, signed_out_at=now))
        session.commit()
        return result.rowcount or 0, session_ids  # ty: ignore[unresolved-attribute]


def end_workos_sessions(session_ids: list[str]) -> int:
    """End each WorkOS session. Best-effort: the tokens are already revoked."""
    api_key = global_config.WORKOS_API_KEY
    if not session_ids:
        return 0
    if not api_key:
        log.warning("sign_out: WORKOS_API_KEY unset; WorkOS sessions left to expire")
        return 0
    ended = 0
    with httpx.Client(
        headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT_SECONDS
    ) as client:
        for session_id in session_ids:
            try:
                response = client.post(
                    WORKOS_REVOKE_SESSION_URL, json={"session_id": session_id}
                )
            except httpx.HTTPError as exc:
                log.warning(
                    "sign_out: WorkOS session revoke failed: {}", type(exc).__name__
                )
                continue
            # 404: the session had already ended, which is the outcome we want.
            if response.is_success or response.status_code == 404:
                ended += 1
            else:
                log.warning(
                    "sign_out: WorkOS session revoke returned {}", response.status_code
                )
    return ended


@service(
    name="sign_out",
    description=(
        "Sign out of DaySurface everywhere: revoke every token DaySurface issued "
        "to the user's apps and end their sign-in, so each connected app must "
        "sign in again. Gmail stays linked (gmail_disconnect does that). "
        "Available only when DaySurface issues the sign-in tokens "
        "(MCP_AUTH_MODE=self); otherwise it refuses."
    ),
    input_model=SignOutInput,
    output_model=SignOutResult,
    mutating=True,
)
def sign_out(input: SignOutInput) -> SignOutResult:
    if not input.user_id:
        raise ClientRefusalError("There is no signed-in user to sign out.")
    if global_config.MCP_AUTH_MODE != "self":
        raise ClientRefusalError(
            "Sign-out is not available on this server: its sign-in tokens are "
            "issued by WorkOS AuthKit and cannot be revoked here. Disconnect "
            "DaySurface in your app instead."
        )
    tokens_revoked, session_ids = _revoke_tokens(input.user_id)
    sessions_ended = end_workos_sessions(session_ids)
    log.info(
        "sign_out revoked {} token(s) and ended {} WorkOS session(s)",
        tokens_revoked,
        sessions_ended,
    )
    return SignOutResult(
        tokens_revoked=tokens_revoked, sign_in_sessions_ended=sessions_ended
    )
