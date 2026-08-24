"""Waitlist signup logic: store the email, then best-effort Resend sync.

Deliberately NOT a ``@service``: joining the waitlist is a public,
unauthenticated HTTP action, so it is exposed only through
``api_server/routes/waitlist.py`` - never as an ``Idempotency-Key``-gated
auto-route, a CLI command, or an MCP tool. The Postgres insert is the commit
point; every Resend call is best-effort and swallows its own failures, so a
Resend outage or an unset key can never fail a signup (the row is already
saved and can be backfilled).
"""

import re

import httpx
from loguru import logger as log
from sqlalchemy.exc import IntegrityError

from common import global_config
from db.engine import use_db_session
from db.models.waitlist_signups import WaitlistSignup

# Pragmatic email shape check - not full RFC 5322, just enough to reject
# obvious garbage without pulling in email-validator. `local@domain.tld`.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RESEND_BASE = "https://api.resend.com"
_HTTP_TIMEOUT = 5.0


def is_valid_email(email: str) -> bool:
    return len(email) <= 320 and bool(_EMAIL_RE.match(email))


def _ok(status_code: int) -> bool:
    """A 2xx is success; 3xx (redirect) and everything else is failure."""
    return 200 <= status_code < 300


def _slack_escape(text: str) -> str:
    """Escape Slack's three control characters so attacker-supplied text (a
    signup email) can't inject a broadcast mention like ``<!channel>``.
    ``&`` must be escaped first."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def record_signup(email: str, source: str | None) -> bool:
    """Insert the signup; return True if newly created, False if already present.

    Dedupe is a DB invariant (unique ``email``): a duplicate raises
    ``IntegrityError``, which is an idempotent success here, not an error.
    """
    normalized = email.strip().lower()
    with use_db_session() as session:
        session.add(WaitlistSignup(email=normalized, source=source))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
    return True


def sync_to_resend(email: str) -> None:
    """Best-effort: add the contact to the Resend audience. Never raises."""
    key = global_config.RESEND_API_KEY
    audience = global_config.RESEND_AUDIENCE_ID
    if not (key and audience):
        return
    try:
        resp = httpx.post(
            f"{_RESEND_BASE}/audiences/{audience}/contacts",
            headers={"Authorization": f"Bearer {key}"},
            json={"email": email, "unsubscribed": False},
            timeout=_HTTP_TIMEOUT,
        )
        if not _ok(resp.status_code):
            log.warning(
                "Resend audience add failed: {} {}", resp.status_code, resp.text[:200]
            )
    except httpx.HTTPError as exc:
        # Network/timeout to a third party: log and move on; the row is saved.
        log.warning("Resend audience add error: {}", type(exc).__name__)


def notify_new_signup(email: str) -> None:
    """Best-effort: post to Slack that someone joined. Never raises."""
    if not global_config.WAITLIST_NOTIFY:
        return
    url = global_config.WAITLIST_SLACK_WEBHOOK_URL
    if not url:
        return
    try:
        resp = httpx.post(
            url,
            json={"text": f"New DaySurface waitlist signup: {_slack_escape(email)}"},
            timeout=_HTTP_TIMEOUT,
        )
        if not _ok(resp.status_code):
            log.warning("Slack notify failed: {} {}", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        log.warning("Slack notify error: {}", type(exc).__name__)


def send_confirmation(email: str) -> None:
    """Best-effort: send the signer a confirmation. Off unless enabled."""
    if not global_config.WAITLIST_CONFIRM_EMAIL:
        return
    key = global_config.RESEND_API_KEY
    sender = global_config.WAITLIST_FROM_EMAIL
    if not (key and sender):
        return
    _send_email(
        key,
        sender,
        email,
        "You're on the DaySurface waitlist",
        "Thanks for joining the DaySurface waitlist - we'll be in touch soon.",
    )


def _send_email(key: str, sender: str, to: str, subject: str, text: str) -> None:
    """Send one transactional email via Resend. Never raises."""
    try:
        resp = httpx.post(
            f"{_RESEND_BASE}/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": sender, "to": [to], "subject": subject, "text": text},
            timeout=_HTTP_TIMEOUT,
        )
        if not _ok(resp.status_code):
            log.warning("Resend send failed: {} {}", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        log.warning("Resend send error: {}", type(exc).__name__)
