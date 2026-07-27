"""Google OAuth start + callback routes.

The ``/start`` endpoint requires authentication and redirects to Google.
The ``/callback`` endpoint is public (Google's redirect is the caller) but
relies on a signed ``state`` token to recover the user_id.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger as log
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser, get_authenticated_user
from common import global_config
from common.token_encryption import require_encryption
from db.engine import get_db_session
from db.models.google_tokens import GoogleToken
from models.gmail import GmailConnectInput
from services.gmail_svc import (
    GOOGLE_TOKEN_ENDPOINT,
    _verify_state,
    gmail_connect,
)

router = APIRouter(prefix="/api/v1/auth/google", tags=["google-oauth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_id_token_email(id_token: str) -> str | None:
    """Extract the ``email`` claim from a Google id_token JWT.

    Signature verification is intentionally skipped: the token came over TLS
    from Google's token endpoint as part of an authorization_code exchange.
    """
    if not id_token or id_token.count(".") < 2:
        return None
    middle = id_token.split(".")[1]
    padding = "=" * (-len(middle) % 4)
    try:
        raw = base64.urlsafe_b64decode(middle + padding)
        claims = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    email = claims.get("email")
    return email if isinstance(email, str) else None


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    # message may include the raw `error` query param from Google's redirect,
    # which is attacker-influenceable; escape before inlining into the page.
    safe = html.escape(message, quote=True)
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<!doctype html><html><body>"
            "<h1>Connection failed</h1>"
            f"<p>{safe}</p>"
            "</body></html>"
        ),
    )


def _success_page(email: str) -> HTMLResponse:
    return HTMLResponse(
        content=(
            "<!doctype html><html><body>"
            "<h1>Connected ✓</h1>"
            f"<p>Gmail is now linked to {email}.</p>"
            # Under stateless HTTP the server cannot push a completion
            # notification to the MCP client (SEP-1036
            # notifications/elicitation/complete), so this page is the user's
            # only completion signal - it must say what to do next.
            "<p>Return to your chat and ask the assistant to retry - "
            "it can use Gmail now. You can close this tab.</p>"
            "</body></html>"
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/start")
def start(
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> RedirectResponse:
    """Kick off the OAuth flow: redirect the user to Google's consent screen."""
    try:
        result = gmail_connect(GmailConnectInput(user_id=user.user_id))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url=result.auth_url, status_code=302)


async def _exchange_code(
    *, code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict | HTMLResponse:
    """POST the authorization code to Google. Return parsed body or an error page."""
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
    except httpx.HTTPError as exc:
        log.warning("Google token exchange failed: {}", exc)
        return _error_page("Failed to reach Google's token endpoint.", 502)

    if resp.status_code != 200:
        log.warning(
            "Google token exchange returned {}: {}", resp.status_code, resp.text
        )
        return _error_page("Google rejected the token exchange.", 400)

    try:
        return resp.json()
    except json.JSONDecodeError:
        return _error_page("Google returned a non-JSON token response.", 502)


def _upsert_token_row(
    session: Session,
    *,
    user_id: str,
    email: str,
    refresh_token_enc: bytes,
    key_id: str,
    scopes: list[str],
) -> None:
    now = datetime.now(UTC)
    row = session.query(GoogleToken).filter_by(user_id=user_id).one_or_none()
    if row is None:
        session.add(
            GoogleToken(
                user_id=user_id,
                email=email or None,
                refresh_token_enc=refresh_token_enc,
                key_id=key_id,
                scopes=scopes,
                granted_at=now,
                revoked_at=None,
            )
        )
    else:
        row.email = email or row.email
        row.refresh_token_enc = refresh_token_enc
        row.key_id = key_id
        row.scopes = scopes
        row.granted_at = now
        row.revoked_at = None
    session.commit()


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Exchange the authorization code for tokens and persist the refresh token."""
    if error:
        return _error_page(f"Google returned an error: {error}")
    if not state:
        return _error_page("Missing OAuth state parameter.")
    user_id = _verify_state(state)
    if user_id is None:
        return _error_page(
            "Invalid or expired OAuth state. Authorization links are only "
            "valid for 10 minutes - go back to your chat and retry the "
            "request to get a fresh link."
        )
    if not code:
        return _error_page("Missing authorization code.")

    client_id = global_config.GOOGLE_CLIENT_ID
    client_secret = global_config.GOOGLE_CLIENT_SECRET
    redirect_uri = global_config.GOOGLE_REDIRECT_URI
    if not (client_id and client_secret and redirect_uri):
        return _error_page("Google OAuth is not configured on the server.", 503)

    body = await _exchange_code(
        code=code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    if isinstance(body, HTMLResponse):
        return body

    refresh_token = body.get("refresh_token")
    if not refresh_token:
        return _error_page(
            "Google did not return a refresh_token. "
            "Revoke the app's access in your Google account and try again."
        )

    email = _decode_id_token_email(body.get("id_token", "")) or ""
    scope_str = body.get("scope", "")
    scopes = scope_str.split() if isinstance(scope_str, str) else []

    enc = require_encryption()
    _upsert_token_row(
        session,
        user_id=user_id,
        email=email,
        refresh_token_enc=enc.encrypt(refresh_token),
        key_id=enc.key_id,
        scopes=scopes,
    )
    _maybe_start_watch(user_id)
    return _success_page(email or "your Google account")


def _log_watch_start_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("Auto-start Gmail watch failed: {}", task.exception())


def _maybe_start_watch(user_id: str) -> None:
    """Fire-and-forget a Gmail watch so a Pub/Sub hiccup never fails OAuth.

    Only runs when push is configured; the watch is best-effort and, if it
    fails here, the periodic renewal loop will re-establish it later.
    """
    if not global_config.GMAIL_PUBSUB_TOPIC:
        return
    # Lazy imports: keep the watch service (and Gmail SDK) off the OAuth import
    # path, and avoid an import cycle through the service registry.
    from models.gmail_watch import GmailWatchStartInput  # noqa: PLC0415
    from services.gmail_watch_svc import gmail_watch_start  # noqa: PLC0415

    task = asyncio.create_task(
        asyncio.to_thread(gmail_watch_start, GmailWatchStartInput(user_id=user_id))
    )
    task.add_done_callback(_log_watch_start_failure)
