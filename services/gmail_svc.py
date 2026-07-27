"""Gmail OAuth services - pure business logic.

Phase 3: connect / status / disconnect + shared helpers.
Phase 4: drafts / inbox / threads / curate services live in sibling modules
(``gmail_drafts_svc`` and ``gmail_messages_svc``) which import the helpers
defined here. All three modules participate in service discovery.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.message import EmailMessage, MIMEPart
from email.utils import getaddresses
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger as log
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.google_tokens import GoogleToken
from models.gmail import (
    AttachmentUpload,
    GmailConnectInput,
    GmailConnectResult,
    GmailDisconnectInput,
    GmailDisconnectResult,
    GmailStatusInput,
    GmailStatusResult,
    InlineImageUpload,
)
from services import ConnectRequiredError, service
from services._gmail_fake_backend import build_fake_gmail_client

# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class GoogleOAuthNotConfiguredError(RuntimeError):
    """GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI missing.

    Subclasses RuntimeError so pre-existing callers catching the old bare
    ``RuntimeError("Google OAuth not configured")`` keep working.
    """

    def __init__(self) -> None:
        super().__init__("Google OAuth not configured")


class GmailNotConnectedError(ConnectRequiredError):
    """Raised when a Gmail-API service is invoked for a user with no active token row.

    The message doubles as the recovery script for the calling host/LLM: over
    MCP it is surfaced verbatim as the ``isError`` tool-result text, which is
    the only channel guaranteed to reach the caller at failure time - so it
    must say what to do next (gmail_connect -> show auth_url -> retry), not
    just what went wrong. Every Gmail-connection-dependent service raises this
    class, so the recovery text lives here rather than at each raise site.
    """

    def __init__(self, user_id: str) -> None:
        super().__init__(
            user_id,
            f"No Gmail account is linked for user_id={user_id!r}. "
            "To recover: call the gmail_connect tool, present the returned "
            "auth_url to the user as a clickable link so they can complete "
            "Google's consent flow, then retry this tool. gmail_status "
            "reports whether the connection is active.",
            elicitation_message="Authorize Gmail access in your browser to continue.",
        )

    def build_auth_url(self) -> str | None:
        """Mint the Google consent URL, or None when OAuth is unconfigured here."""
        try:
            return gmail_connect(GmailConnectInput(user_id=self.user_id)).auth_url
        except GoogleOAuthNotConfiguredError:
            return None


class GmailAttachmentTooLargeError(Exception):
    """Raised when a fetched attachment exceeds the configured size ceiling.

    Carries the sizes so a transport can build a precise client-facing message
    (the HTTP API maps this to 413 Payload Too Large).
    """

    def __init__(self, *, attachment_id: str, size: int, max_bytes: int) -> None:
        self.attachment_id = attachment_id
        self.size = size
        self.max_bytes = max_bytes
        super().__init__(
            f"Attachment {attachment_id} is {size} bytes, over the {max_bytes}-byte "
            "limit (global_config.gmail.max_attachment_bytes). Raise the limit or "
            "handle this file out of band."
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# gmail.modify is a restricted scope that already grants read, compose-draft,
# and send. Requesting readonly/compose/send on top is redundant and Google's
# OAuth verification rejects apps asking for broader-than-needed scopes, so we
# request the single minimal superset. See docs/.../oauth verification notes.
GMAIL_SCOPES: list[str] = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.modify",
]

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

_STATE_MAX_AGE_SECONDS = 10 * 60  # 10 minutes


# ---------------------------------------------------------------------------
# State signing (HMAC-SHA256 over JSON payload, key = SESSION_SECRET_KEY)
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _hmac_key() -> bytes:
    return global_config.SESSION_SECRET_KEY.encode("utf-8")


def _sign_state(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "nonce": secrets.token_urlsafe(16),
        "issued_at": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_hmac_key(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def _verify_state(state: str) -> str | None:
    """Return the embedded ``user_id`` if the state is valid + fresh, else None."""
    if not state or "." not in state:
        return None
    try:
        payload_b64, sig_b64 = state.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None

    expected_sig = hmac.new(_hmac_key(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    user_id = payload.get("user_id")
    issued_at = payload.get("issued_at")
    if not isinstance(user_id, str) or not isinstance(issued_at, int):
        return None
    if time.time() - issued_at > _STATE_MAX_AGE_SECONDS:
        return None
    return user_id


# ---------------------------------------------------------------------------
# DB helpers (shared with Phase 4)
# ---------------------------------------------------------------------------


@contextmanager
def _get_db_session() -> Generator[Session, None, None]:
    """Open a database session.

    Thin wrapper around ``db.engine.use_db_session`` so Phase 4 has a single
    obvious import to reach for.
    """
    with use_db_session() as session:
        yield session


def _load_token_row(session: Session, user_id: str) -> GoogleToken | None:
    """Return the active (non-revoked) GoogleToken row for a user, or None.

    "Active" means both un-revoked *and* still holding a credential: disconnect
    erases ``refresh_token_enc``, so a row with a NULL ciphertext has nothing to
    authenticate with. Filtering it here keeps every caller consistent - status
    can't report a connection the client would then fail closed on.
    """
    return (
        session.query(GoogleToken)
        .filter(
            GoogleToken.user_id == user_id,
            GoogleToken.revoked_at.is_(None),
            GoogleToken.refresh_token_enc.isnot(None),
        )
        .one_or_none()
    )


def _account_email(user_id: str) -> str | None:
    """Return the connected account's own email address, or None if unknown.

    Sourced from the stored OAuth token row (populated at connect time from the
    OpenID ``email`` claim) rather than an extra ``users.getProfile`` round-trip.
    Used to keep the account owner out of a reply's default recipients.
    """
    with _get_db_session() as session:
        row = _load_token_row(session, user_id)
        return row.email if row is not None else None


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_connect",
    description="Begin the Google OAuth flow to link a Gmail account",
    input_model=GmailConnectInput,
    output_model=GmailConnectResult,
)
def gmail_connect(input: GmailConnectInput) -> GmailConnectResult:
    """Build the Google OAuth authorization URL for the user."""
    client_id = global_config.GOOGLE_CLIENT_ID
    redirect_uri = global_config.GOOGLE_REDIRECT_URI
    if not client_id or not redirect_uri:
        raise GoogleOAuthNotConfiguredError

    state = _sign_state(input.user_id)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"
    return GmailConnectResult(auth_url=auth_url, state=state)


@service(
    name="gmail_status",
    description="Return whether the user has a linked Gmail account",
    input_model=GmailStatusInput,
    output_model=GmailStatusResult,
)
def gmail_status(input: GmailStatusInput) -> GmailStatusResult:
    """Look up the current Gmail connection status for a user."""
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is None:
            return GmailStatusResult(connected=False)
        return GmailStatusResult(
            connected=True,
            email=row.email,
            scopes=list(row.scopes or []),
            granted_at=row.granted_at,
        )


@service(
    name="gmail_disconnect",
    description="Revoke and remove the user's linked Gmail account",
    input_model=GmailDisconnectInput,
    output_model=GmailDisconnectResult,
    mutating=True,
)
def _revoke_with_google(row: GoogleToken) -> None:
    """Best-effort remote revoke of a row's refresh token. Never raises.

    Failures are non-fatal by design: the caller still erases and marks the row
    revoked locally, so the user is never stuck in a half-connected state.
    """
    try:
        # Call-time import: tests patch
        # common.token_encryption.require_encryption, so binding it at
        # module import would bypass the patch.
        from common.token_encryption import require_encryption  # noqa: PLC0415

        enc = require_encryption()
        if row.refresh_token_enc is None:
            raise ValueError("token already erased")  # noqa: TRY301
        refresh_token = enc.decrypt(row.refresh_token_enc)
        httpx.post(
            GOOGLE_REVOKE_ENDPOINT,
            params={"token": refresh_token},
            timeout=10.0,
        )
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Google token revoke failed; revoking locally anyway: {}", exc)
    except Exception as exc:  # noqa: BLE001
        # Defensive boundary: decrypt() or require_encryption() may raise
        # provider-specific errors (cryptography.fernet.InvalidToken,
        # RuntimeError when key missing in prod). We MUST still mark the
        # row revoked locally so the user can recover.
        log.warning("Google revoke errored ({}): proceeding with local revoke", exc)


def gmail_disconnect(input: GmailDisconnectInput) -> GmailDisconnectResult:
    """Revoke the stored refresh token with Google + erase every trace of it.

    If the network revoke fails we still mark the row revoked locally so the
    user is never stuck in a half-connected state.

    The purges below run whether or not there was a live connection to revoke,
    which makes disconnect a retry-safe cleanup: they are best-effort, so a
    purge that failed once (or a connection revoked before this cleanup
    existed) would otherwise leave banked content stranded with no way to
    reach it. ``revoked`` still reports only whether a live token was revoked.
    """
    revoked = False
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is not None:
            _revoke_with_google(row)
            # Erase the secret itself, not just the flag: a revoked row has no
            # use for the ciphertext, and keeping it means a disconnect leaves
            # the user's Google credential sitting at rest indefinitely.
            # Reconnecting writes a fresh token via _upsert_token_row.
            row.refresh_token_enc = None
            row.revoked_at = datetime.now(UTC)
            session.commit()
            _invalidate_gmail_client(input.user_id)
            revoked = True

    # Purge all banked curation for this user: disconnecting Gmail must leave no
    # derived inbox content behind. Call-time import avoids a module-load cycle
    # (curation_ledger is DB-only and does not import this module). Best-effort:
    # the token is already revoked+committed above, so a purge failure must not
    # turn a successful disconnect into a server error (same rationale as
    # mark_state_best_effort) - a later disconnect retries the cleanup.
    from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

    from services.curation_ledger import purge_user  # noqa: PLC0415

    try:
        purged = purge_user(input.user_id)
        if purged:
            log.debug("Purged {} curation rows for user {}", purged, input.user_id)
    except SQLAlchemyError as exc:
        log.warning(
            "Curation purge failed for user {} (disconnect still succeeded): {}",
            input.user_id,
            exc,
        )

    # Same rule for banked push events: webhook_events payloads carry the
    # subject / sender / snippet of every notified message as plaintext JSON,
    # so they must go the same way as the curation ledger. Best-effort for the
    # same reason - the disconnect itself is already committed.
    from services.webhooks_svc import purge_user_events  # noqa: PLC0415

    try:
        events, deliveries = purge_user_events(input.user_id)
        if events or deliveries:
            log.debug(
                "Purged {} webhook events / {} deliveries for user {}",
                events,
                deliveries,
                input.user_id,
            )
    except SQLAlchemyError as exc:
        log.warning(
            "Webhook event purge failed for user {} (disconnect still succeeded): {}",
            input.user_id,
            exc,
        )
    return GmailDisconnectResult(revoked=revoked)


# ---------------------------------------------------------------------------
# Gmail-API client + MIME helpers (shared by drafts / inbox / threads svcs)
# ---------------------------------------------------------------------------


def _mint_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for a short-lived access token via Google.

    Pure-sync ``httpx.Client`` keeps the helper callable from sync services.
    """
    client_id = global_config.GOOGLE_CLIENT_ID
    client_secret = global_config.GOOGLE_CLIENT_SECRET
    if not client_id or not client_secret:
        raise GoogleOAuthNotConfiguredError

    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    resp.raise_for_status()
    body = resp.json()
    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Google token endpoint returned no access_token")
    return access_token


_client_cache: dict[str, tuple[float, Any]] = {}
_client_cache_lock = threading.Lock()  # guards all _client_cache reads/writes
_CLIENT_TTL_S = 50 * 60  # 50 min; access tokens live ~60 min
_CLIENT_CACHE_MAX = 256  # hard bound on cached clients (one entry per user)


def _cached_client(user_id: str):  # noqa: ANN202 - googleapiclient Resource is dynamic
    """Return the live cached client for ``user_id``, or ``None``.

    Every lookup also purges expired entries, so tuples for users who stopped
    calling are reclaimed promptly (the bound alone already caps memory).
    """
    now = time.time()
    with _client_cache_lock:
        for key in [k for k, (exp, _) in _client_cache.items() if now >= exp]:
            del _client_cache[key]
        entry = _client_cache.get(user_id)
        return entry[1] if entry is not None else None


def _store_client(user_id: str, client: Any) -> None:
    """Cache ``client`` for ``user_id``, keeping the cache bounded.

    When full, the entry closest to expiry (i.e. oldest, since the TTL is
    fixed) is evicted to make room.
    """
    with _client_cache_lock:
        if user_id not in _client_cache and len(_client_cache) >= _CLIENT_CACHE_MAX:
            oldest = min(_client_cache, key=lambda k: _client_cache[k][0])
            del _client_cache[oldest]
        _client_cache[user_id] = (time.time() + _CLIENT_TTL_S, client)


def _maybe_fake_gmail_client():  # noqa: ANN202 - fake mirrors the dynamic Resource
    """Return a fixture-serving fake Gmail client when the e2e fake backend is
    explicitly enabled, else ``None``.

    Gated on ``GMAIL_FAKE_BACKEND=1``. Hard-refuses under ``DEV_ENV=prod`` so the
    fake can never stand in for a real mailbox in production, no matter how the
    env is set. Used only by the MCP-App e2e harness to render Gmail apps offline
    (no linked account / OAuth / network); see ``services/_gmail_fake_backend.py``.
    """
    if os.environ.get("GMAIL_FAKE_BACKEND") != "1":
        return None
    if global_config.DEV_ENV == "prod":
        raise RuntimeError(
            "GMAIL_FAKE_BACKEND must never be set in production (DEV_ENV=prod)"
        )
    log.warning(
        "GMAIL_FAKE_BACKEND active: serving fixture Gmail data, not a real mailbox"
    )
    return build_fake_gmail_client()


def _get_gmail_client(user_id: str):  # noqa: ANN202 - googleapiclient Resource is dynamic
    """Return an authorized ``googleapiclient`` Gmail v1 service for ``user_id``.

    Caches the built client per user for ``_CLIENT_TTL_S`` seconds to avoid
    repeated token-mint + discovery-build overhead (~200-500ms each). The
    cache is bounded to ``_CLIENT_CACHE_MAX`` entries and purges expired
    entries on every lookup.

    Cross-process disconnect: ``_invalidate_gmail_client`` only clears this
    process's cache, so *every* call - cache hit or miss - starts with one
    cheap indexed read of the token row. If another worker/replica revoked or
    deleted the row (``gmail_disconnect``), the stale entry is dropped and
    ``GmailNotConnectedError`` is raised (fail closed) instead of serving an
    authorized client for up to the TTL. Two deliberate tradeoffs:

    - DB coupling: a cache hit used to touch no infrastructure, so Gmail
      kept working through a DB blip for already-connected users. Now every
      Gmail call (including the webhook path) needs the DB read, so DB down
      means Gmail down instantly. Fail-closed is the point: availability is
      traded for never serving a disconnected account.
    - Residual Google-side caveat: this check only covers state in *this*
      DB - an already-minted access token stays valid Google-side until
      Google honors the ``gmail_disconnect`` remote revoke (or the ~60-min
      token lifetime lapses), so full cutoff still relies on Google-side
      revocation.

    Concurrency: two threads missing for the same user may both mint an
    access token and build a client; the duplicate wastes one ~300ms token
    exchange (Google tolerates multiple outstanding access tokens) and
    ``_store_client`` is last-write-wins. This matches main's pre-cache-fix
    status quo and avoids head-of-line blocking every user's cold path
    behind one slow Google response.

    Raises ``GmailNotConnectedError`` if no active token row exists. Network
    or Google-side errors propagate so the caller can decide how to surface them.
    """
    fake = _maybe_fake_gmail_client()
    if fake is not None:
        return fake

    # One unconditional cheap DB read per call: fail closed immediately if
    # the token row was revoked/deleted (possibly by another process).
    with _get_db_session() as session:
        row = _load_token_row(session, user_id)
        # A NULL ciphertext means the row was erased by a disconnect; treat it
        # exactly like a missing row rather than handing None to decrypt().
        if row is None or row.refresh_token_enc is None:
            _invalidate_gmail_client(user_id)
            raise GmailNotConnectedError(user_id)
        encrypted = row.refresh_token_enc

    client = _cached_client(user_id)
    if client is not None:
        return client

    # Deliberate deferral: the Google SDK (discovery machinery) is heavy -
    # only load it when a Gmail API call is actually made, not at service
    # discovery / module import.
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    # Call-time import: tests patch common.token_encryption.require_encryption.
    from common.token_encryption import require_encryption  # noqa: PLC0415

    refresh_token = require_encryption().decrypt(encrypted)
    access_token = _mint_access_token(refresh_token)
    creds = Credentials(token=access_token)
    client = build("gmail", "v1", credentials=creds, cache_discovery=False)
    _store_client(user_id, client)
    return client


def _invalidate_gmail_client(user_id: str) -> None:
    """Remove a cached client (call after disconnect or token revocation).

    Per-process only: other workers/replicas rely on the cache-hit token-row
    check in ``_get_gmail_client`` to drop their own stale entries.
    """
    with _client_cache_lock:
        _client_cache.pop(user_id, None)


def _build_raw_message(
    *,
    to: str,
    subject: str,
    body: str,
    body_html: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to_thread_id: str | None = None,  # noqa: ARG001 - threadId travels on the wrapper, not headers
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[AttachmentUpload] | None = None,
    inline_images: list[InlineImageUpload] | None = None,
) -> str:
    """Return a base64-url-encoded MIME message for ``drafts.create`` / ``messages.send``.

    For replies, supply ``in_reply_to`` (the parent ``Message-ID``) and ``references``
    (the parent's existing ``References`` plus its ``Message-ID``) so MUAs other
    than Gmail also thread the conversation. Gmail itself uses ``threadId`` on
    the API wrapper; these headers are belt-and-braces for the recipient.

    Body shape: pass ``body`` (plain text) and/or ``body_html``. With both, the
    message is multipart/alternative; with only ``body_html`` it is an HTML
    message (so HTML-only drafts survive a rebuild); otherwise it is plain text.

    When ``attachments`` is non-empty the message additionally becomes
    multipart/mixed. Each attachment is an ``AttachmentUpload`` with
    ``filename``, ``mime_type``, and base64url ``data_base64``.
    """
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    body_host = _set_message_body(msg, body, body_html)
    # Inline images go in a multipart/related around the HTML part so the body's
    # cid: references resolve; regular files become multipart/mixed attachments.
    # The image must relate to the HTML part itself, not a multipart/alternative
    # root, or clients showing the HTML alternative can't find the image.
    for img in inline_images or []:
        maintype, subtype = _split_mime(img.mime_type)
        body_host.add_related(
            _decode_b64url(img.data_base64),
            maintype=maintype,
            subtype=subtype,
            cid=f"<{img.content_id}>",
        )
    for att in attachments or []:
        maintype, subtype = _split_mime(att.mime_type)
        msg.add_attachment(
            _decode_b64url(att.data_base64),
            maintype=maintype,
            subtype=subtype,
            filename=att.filename,
        )

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _decode_b64url(data: str) -> bytes:
    """Decode base64url, re-padding per RFC 4648 §5."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _b64url_to_std(data: str) -> str:
    """Convert Gmail's padding-stripped base64url to standard, padded base64.

    Gmail returns attachment / inline-image bytes as base64url with padding
    stripped; every consumer that wants to hand those bytes to a browser data:
    URI or a standard base64 decoder needs this exact normalization.
    """
    std = data.replace("-", "+").replace("_", "/")
    return std + "=" * (-len(std) % 4)


def _split_mime(mime_type: str) -> tuple[str, str]:
    """Split ``maintype/subtype``, defaulting to ``application/octet-stream``."""
    maintype, _, subtype = mime_type.partition("/")
    if not subtype:
        return "application", "octet-stream"
    return maintype, subtype


def _set_message_body(msg: EmailMessage, body: str, body_html: str | None) -> MIMEPart:
    """Set the body and return the part inline images should relate to.

    For plain+HTML the host is the HTML alternative subpart (so inline images
    nest as ``alternative -> [plain, related -> [html, image]]``, the broadly
    compatible shape); otherwise it is the message root.
    """
    if body_html and body:
        msg.set_content(body, subtype="plain", charset="utf-8")
        msg.add_alternative(body_html, subtype="html", charset="utf-8")
        return list(msg.iter_parts())[-1]  # the HTML alternative subpart
    if body_html:
        msg.set_content(body_html, subtype="html", charset="utf-8")
        return msg
    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg


def _headers_to_dict(headers: list[dict[str, str]] | None) -> dict[str, str]:
    """Flatten Gmail's ``[{name, value}, ...]`` header list to a lower-cased dict."""
    out: dict[str, str] = {}
    for h in headers or []:
        name = h.get("name")
        value = h.get("value")
        if isinstance(name, str) and isinstance(value, str):
            out[name.lower()] = value
    return out


def _addresses(header_value: str | None) -> list[tuple[str, str]]:
    """Parse an address header into ``[(display_name, email), ...]`` pairs.

    Drops entries with no email address (e.g. a stray group syntax remnant).
    """
    if not header_value:
        return []
    return [(name, addr) for name, addr in getaddresses([header_value]) if addr]


def _decode_body_data(data: str | None) -> str | None:
    if not data:
        return None
    # Gmail returns base64url with padding stripped; re-add per RFC 4648 5.
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", "replace"
        )
    except (binascii.Error, ValueError):
        return None


def _walk_parts(part: dict[str, Any], out: dict[str, Any]) -> None:
    """Recursively visit a Gmail payload tree, populating ``out`` in place."""
    mime_type = part.get("mimeType", "")
    body = part.get("body", {}) or {}
    filename = part.get("filename") or ""
    part_headers = _headers_to_dict(part.get("headers"))
    content_id = part_headers.get("content-id")
    if content_id:
        content_id = content_id.strip("<>")

    is_inline_image = mime_type.startswith("image/") and content_id and not filename

    if is_inline_image:
        raw_data = body.get("data")
        b64_data: str | None = _b64url_to_std(raw_data) if raw_data else None
        out["attachments"].append(
            {
                "filename": None,
                "mime_type": mime_type or None,
                "size": body.get("size"),
                "attachment_id": body.get("attachmentId"),
                "content_id": content_id,
                "data": b64_data,
            }
        )
    elif filename and (body.get("attachmentId") or body.get("size")):
        raw_data = body.get("data")
        b64_data = (
            _b64url_to_std(raw_data)
            if raw_data and mime_type.startswith("image/")
            else None
        )
        out["attachments"].append(
            {
                "filename": filename or None,
                "mime_type": mime_type or None,
                "size": body.get("size"),
                "attachment_id": body.get("attachmentId"),
                "content_id": content_id,
                "data": b64_data,
            }
        )
    elif mime_type == "text/plain" and out["body_text"] is None:
        out["body_text"] = _decode_body_data(body.get("data"))
    elif mime_type == "text/html" and out["body_html"] is None:
        out["body_html"] = _decode_body_data(body.get("data"))

    for child in part.get("parts", []) or []:
        _walk_parts(child, out)


def _parse_message_resource(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized dict from a Gmail ``messages.get`` / ``drafts.get`` body.

    Returns a dict with keys: ``message_id``, ``thread_id``, ``snippet``,
    ``from``, ``to``, ``cc``, ``subject``, ``date`` (datetime|None),
    ``body_text``, ``body_html``, ``attachments`` (list[dict]).
    """
    payload = msg.get("payload") or {}
    headers = _headers_to_dict(payload.get("headers"))

    out: dict[str, Any] = {
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "snippet": msg.get("snippet"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        # Drafts retain Bcc in their stored MIME (it is only stripped once sent),
        # so format=full surfaces it here and rebuilds can preserve it.
        "bcc": headers.get("bcc"),
        "subject": headers.get("subject"),
        # Reply threading headers, so a rebuild of a reply draft keeps non-Gmail
        # MUAs threading the conversation.
        "in_reply_to": headers.get("in-reply-to"),
        "references": headers.get("references"),
        "date": None,
        "body_text": None,
        "body_html": None,
        "attachments": [],
    }

    internal_date = msg.get("internalDate")
    if internal_date is not None:
        try:
            ts_ms = int(internal_date)
            out["date"] = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
        except (TypeError, ValueError):
            out["date"] = None

    _walk_parts(payload, out)
    return out
