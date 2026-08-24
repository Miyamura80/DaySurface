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
from html import escape

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

# Shared confirmation-email copy, defined once and interpolated into both the
# plain-text and HTML bodies so a copy edit can't leave them out of sync. The
# intro is split around the client list because the two bodies render that part
# differently on purpose: plain text names the clients, HTML shows their icons.
_INTRO_LEAD = (
    "Thanks for joining the DaySurface waitlist. DaySurface puts a ranked inbox, "
    "a real reply composer, and fill-and-sign PDFs right inside "
)
_INTRO_TAIL = " We'll email you the moment your invite is ready."
# Client icons shown in the HTML intro (hosted PNGs - email clients don't render
# SVG or data: URIs). Order + alt text; the plain-text intro names them instead.
_INTRO_CLIENTS = (("claude", "Claude"), ("chatgpt", "ChatGPT"), ("goose", "Goose"))
# Trailing clause shared by both bodies so they can't drift: the text intro
# prefixes it with the client names, the HTML intro prefixes it with the icons.
_MCP_CLIENT_SUFFIX = "or any MCP client."
_INTRO_CLIENTS_TEXT = f"Claude, ChatGPT, Goose, {_MCP_CLIENT_SUFFIX}"
_CONFIRM_CALLOUT = (
    "DaySurface is fully open source - self-host it and connect your own client "
    "to get started right now."
)
_CONFIRM_DISCLAIMER = (
    "You're getting this because you joined the waitlist at daysurface.com. "
    "If that wasn't you, you can ignore this email."
)


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
    text, html = _confirmation_content()
    _send_email(
        key,
        sender,
        email,
        "You're on the DaySurface waitlist \U0001f30a",
        text,
        html=html,
    )


def _confirmation_content() -> tuple[str, str]:
    """Return the ``(plain_text, html)`` bodies for the confirmation email.

    Pure (no network) so it is unit-testable. The repo/docs links come from the
    central config (``branding.repository_url`` / ``ask.docs_base_url``) so they
    can't drift from the rest of the site, and the shared copy phrases are
    interpolated into both bodies from single constants. The HTML uses only
    inline styles and a table skeleton for broad email-client support, and leads
    with the open-source / self-host callout so a keen signer can start now.
    """
    repo = global_config.branding.repository_url
    docs = global_config.ask.docs_base_url
    base = global_config.branding.website_url.rstrip("/")
    # Escaped forms for interpolation into HTML attributes; the text body keeps
    # the raw URLs. Config is trusted, but a stray quote must not break markup.
    repo_h = escape(repo, quote=True)
    docs_h = escape(docs, quote=True)
    base_h = escape(base, quote=True)
    # Bold "open source" in the HTML rendering of the shared callout sentence.
    callout_html = _CONFIRM_CALLOUT.replace(
        "open source", "<strong>open source</strong>", 1
    )
    # Client icons for the HTML intro (alt text shows when images are blocked).
    icons = "".join(
        f'<img src="{base_h}/logos/email/{slug}.png" width="22" height="22" '
        f'alt="{alt}" style="vertical-align:middle;margin:0 3px;border:0;display:inline-block;">'
        for slug, alt in _INTRO_CLIENTS
    )
    gh_icon = (
        f'<img src="{base_h}/logos/email/github.png" width="16" height="16" alt="" '
        'style="vertical-align:middle;margin-right:8px;border:0;">'
    )
    text = (
        f"You're on the DaySurface waitlist.\n\n"
        f"{_INTRO_LEAD}{_INTRO_CLIENTS_TEXT}{_INTRO_TAIL}\n\n"
        f"Don't want to wait? {_CONFIRM_CALLOUT}\n"
        f"  {repo}\n"
        f"Docs: {docs}\n\n"
        f"{_CONFIRM_DISCLAIMER}"
    )
    html = f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#f4f4f5;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">You're on the DaySurface waitlist - and it's open source, so you can self-host today.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:24px 0;"><tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="width:480px;max-width:92%;background:#ffffff;border:1px solid #e5e5e5;border-radius:12px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="padding:28px 32px 0;"><div style="font-weight:800;font-size:18px;letter-spacing:-0.02em;color:#0a0a0a;">DaySurface</div></td></tr>
<tr><td style="padding:16px 32px 0;">
<h1 style="margin:0;font-size:24px;line-height:1.25;color:#0a0a0a;">You're on the list \U0001f389</h1>
<p style="margin:14px 0 0;font-size:15px;line-height:1.6;color:#3f3f46;">{_INTRO_LEAD}<span style="white-space:nowrap;">{icons}</span> {_MCP_CLIENT_SUFFIX}{_INTRO_TAIL}</p>
</td></tr>
<tr><td style="padding:22px 32px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0fdfc;border:1px solid #c3fffd;border-radius:10px;"><tr><td style="padding:18px 20px;">
<div style="font-weight:700;font-size:14px;color:#0a0a0a;">Don't want to wait?</div>
<p style="margin:6px 0 14px;font-size:14px;line-height:1.55;color:#3f3f46;">{callout_html}</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="border-radius:8px;background:#0a0a0a;"><a href="{repo_h}" style="display:inline-block;padding:11px 20px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">{gh_icon}Self-host on GitHub &rarr;</a></td></tr></table>
<a href="{docs_h}" style="display:inline-block;margin-top:10px;font-size:13px;color:#0a7c78;text-decoration:underline;">Read the docs</a>
</td></tr></table>
</td></tr>
<tr><td style="padding:24px 32px 28px;"><p style="margin:0;font-size:12px;line-height:1.5;color:#a1a1aa;">{_CONFIRM_DISCLAIMER}</p></td></tr>
</table></td></tr></table></body></html>"""
    return text, html


def _send_email(
    key: str, sender: str, to: str, subject: str, text: str, html: str | None = None
) -> None:
    """Send one transactional email via Resend. Never raises."""
    payload: dict[str, object] = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    try:
        resp = httpx.post(
            f"{_RESEND_BASE}/emails",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=_HTTP_TIMEOUT,
        )
        if not _ok(resp.status_code):
            log.warning("Resend send failed: {} {}", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        log.warning("Resend send error: {}", type(exc).__name__)
