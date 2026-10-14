"""Browser leg of DaySurface's OAuth server: consent page and WorkOS sign-in.

    /authorize (MCP SDK) ──► GET  /oauth/consent      shows which client is asking
                             POST /oauth/consent      Allow ──► WorkOS sign-in
    WorkOS ────────────────► GET  /oauth/workos/callback
                             ──► client redirect_uri?code=..&state=..&iss=..

Consent is required before forwarding to WorkOS: DaySurface reaches WorkOS with
one static client id, and WorkOS signs a returning user in without a prompt, so
without this page any client could obtain a code for a signed-in user (MCP spec,
confused deputy). The page names the client and the redirect host, and warns
when the redirect is a loopback address.

Each sign-in is bound to the browser that started it. The consent page sets a
cookie named for that sign-in, holding a random nonce; the consent form and the
WorkOS state carry the same nonce, so a cross-site form post or a callback
replayed in another browser is refused. In production the cookie uses the
``__Host-`` prefix, so no other subdomain can plant or overwrite it. The hop to
WorkOS also uses PKCE, with a verifier derived from the nonce and the server
secret, so a leaked WorkOS code cannot be redeemed through another sign-in.

Until the pending request has been verified, errors render a page instead of
redirecting anywhere.
"""

import base64
import hashlib
import hmac
import html
import re
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from loguru import logger as log
from mcp.server.auth.provider import construct_redirect_uri

from api_server.oauth import cimd, signing
from api_server.oauth.provider import store_authorization_code, user_signed_out_since
from common import global_config

WORKOS_AUTHORIZE_URL = "https://api.workos.com/user_management/authorize"
WORKOS_AUTHENTICATE_URL = "https://api.workos.com/user_management/authenticate"
_WORKOS_TIMEOUT_SECONDS = 10.0
_FLOW_ID = re.compile(r"[0-9a-f]{16}")
# Inline styles only; no script. frame-ancestors stops the Allow button from
# being framed (clickjacking).
_PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"

router = APIRouter(prefix="/oauth", tags=["oauth"])


def issuer_url() -> str:
    """This authorization server's issuer: the origin of the public MCP URL."""
    from api_server.auth.authkit_auth import mcp_resource_url  # noqa: PLC0415

    parts = urlsplit(mcp_resource_url())
    return f"{parts.scheme}://{parts.netloc}"


def _callback_url() -> str:
    return f"{issuer_url()}/oauth/workos/callback"


def _secure_cookies() -> bool:
    return not global_config.is_dev


def cookie_name(flow_id: str) -> str:
    # __Host- requires Secure, Path=/ and no Domain, so it is production-only;
    # local development is served over plain http.
    prefix = "__Host-" if _secure_cookies() else ""
    return f"{prefix}ds_oauth_{flow_id}"


def _workos_pkce_verifier(nonce: str) -> str:
    """The PKCE verifier for the WorkOS hop, recomputable only by this server."""
    digest = hmac.new(
        global_config.SESSION_SECRET_KEY.encode(),
        b"workos-pkce:" + nonce.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    content = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;"
        "padding:0 1rem;line-height:1.5;color:#1f2328}"
        ".warn{background:#fff8c5;border:1px solid #d4a72c;padding:.75rem;border-radius:6px}"
        "button{font-size:1rem;padding:.5rem 1.25rem;margin-right:.5rem;border-radius:6px;"
        "border:1px solid #1f2328;cursor:pointer}"
        ".allow{background:#1f2328;color:#fff}code{word-break:break-all}</style>"
        f"</head><body>{body}</body></html>"
    )
    response = HTMLResponse(content=content, status_code=status_code)
    response.headers["Content-Security-Policy"] = _PAGE_CSP
    response.headers["Cache-Control"] = "no-store"
    return response


def _error_page(message: str) -> HTMLResponse:
    return _page(
        "Sign-in failed",
        "<h1>Sign-in failed</h1>"
        f"<p>{html.escape(message)}</p>"
        "<p>Return to your app and connect DaySurface again.</p>",
        status_code=400,
    )


def _redirect_to_client(
    pending: dict, flow_id: str, **params: str | None
) -> RedirectResponse:
    """Send the browser back to the client, with ``iss`` (RFC 9207) and its state."""
    location = construct_redirect_uri(
        pending["redirect_uri"], state=pending.get("state"), iss=issuer_url(), **params
    )
    response = RedirectResponse(location, status_code=303)
    response.delete_cookie(
        cookie_name(flow_id), path="/", secure=_secure_cookies(), httponly=True
    )
    return response


def _nonce_matches(request: Request, flow_id: object, presented: object) -> bool:
    if not isinstance(flow_id, str) or not _FLOW_ID.fullmatch(flow_id):
        return False
    if not isinstance(presented, str):
        return False
    cookie = request.cookies.get(cookie_name(flow_id))
    # Bytes, so a non-ASCII value fails the comparison instead of raising.
    return bool(cookie) and hmac.compare_digest(cookie.encode(), presented.encode())


@router.get("/consent", include_in_schema=False)
def consent_page(
    request: Request, request_token: str = Query(alias="request")
) -> Response:
    try:
        pending = signing.load_pending_authorization(request_token)
        client = cimd.resolve_client(pending["client_id"])
    except (signing.InvalidSignedValueError, cimd.ClientMetadataError, KeyError):
        return _error_page("This sign-in link is invalid or has expired.")

    client_name = (
        client.client_name or urlsplit(pending["client_id"]).hostname or "An app"
    )
    client_host = urlsplit(pending["client_id"]).hostname or ""
    redirect_host = urlsplit(pending["redirect_uri"]).hostname or ""
    warning = ""
    if cimd.is_loopback_redirect(pending["redirect_uri"]):
        warning = (
            "<p class='warn'>This app sends you back to a program on this computer "
            f"(<code>{html.escape(redirect_host)}</code>). Only continue if you started "
            "this from an app you installed and trust.</p>"
        )
    flow_id = secrets.token_hex(8)
    nonce = secrets.token_urlsafe(24)
    response = _page(
        "Connect to DaySurface",
        "<h1>Connect to DaySurface</h1>"
        f"<p><strong>{html.escape(client_name)}</strong> "
        f"(<code>{html.escape(client_host)}</code>) wants to access your DaySurface "
        "account, including the Gmail account you linked.</p>"
        f"<p>After you sign in, you will be sent to <code>{html.escape(redirect_host)}</code>.</p>"
        f"{warning}"
        "<form method='post' action='/oauth/consent'>"
        f"<input type='hidden' name='request' value='{html.escape(request_token, quote=True)}'>"
        f"<input type='hidden' name='flow' value='{flow_id}'>"
        f"<input type='hidden' name='nonce' value='{html.escape(nonce, quote=True)}'>"
        "<button class='allow' name='decision' value='allow' type='submit'>Allow</button>"
        "<button name='decision' value='deny' type='submit'>Deny</button>"
        "</form>",
    )
    response.set_cookie(
        cookie_name(flow_id),
        nonce,
        max_age=signing.MAX_AGE_SECONDS,
        path="/",
        secure=_secure_cookies(),
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/consent", include_in_schema=False)
def consent_decision(
    request: Request,
    request_token: str = Form(alias="request"),
    flow: str = Form(),
    nonce: str = Form(),
    decision: str = Form(),
) -> Response:
    try:
        pending = signing.load_pending_authorization(request_token)
    except signing.InvalidSignedValueError:
        return _error_page("This sign-in link is invalid or has expired.")
    if not _nonce_matches(request, flow, nonce):
        return _error_page("This request did not come from the consent page.")
    if decision != "allow":
        return _redirect_to_client(pending, flow, error="access_denied")

    state = signing.sign_workos_state({**pending, "flow": flow, "nonce": nonce})
    query = urlencode(
        {
            "response_type": "code",
            "client_id": global_config.WORKOS_CLIENT_ID or "",
            "redirect_uri": _callback_url(),
            "provider": "authkit",
            "state": state,
            "code_challenge": _pkce_challenge(_workos_pkce_verifier(nonce)),
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{WORKOS_AUTHORIZE_URL}?{query}", status_code=303)


def _authenticate_with_workos(code: str, code_verifier: str) -> tuple[str, str | None]:
    """Exchange a WorkOS code for the user id and WorkOS session id."""
    response = httpx.post(
        WORKOS_AUTHENTICATE_URL,
        json={
            "client_id": global_config.WORKOS_CLIENT_ID,
            "client_secret": global_config.WORKOS_API_KEY,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
        },
        timeout=_WORKOS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    user_id = body["user"]["id"]
    session_id = None
    access_token = body.get("access_token")
    if isinstance(access_token, str):
        # Received directly from WorkOS over TLS on an authenticated call, so
        # the session id is read without re-verifying the signature.
        claims = jwt.decode(access_token, options={"verify_signature": False})
        sid = claims.get("sid")
        session_id = sid if isinstance(sid, str) else None
    return user_id, session_id


@router.get("/workos/callback", include_in_schema=False)
def workos_callback(
    request: Request,
    state: str = Query(default=""),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    try:
        pending, allowed_at = signing.load_workos_state(state)
    except signing.InvalidSignedValueError:
        return _error_page("This sign-in link is invalid or has expired.")
    flow_id = pending.get("flow")
    if not _nonce_matches(request, flow_id, pending.get("nonce")):
        return _error_page("Sign-in must finish in the browser where it started.")
    flow_id = str(flow_id)  # a 16-hex-digit string, checked by _nonce_matches
    if error or not code:
        return _redirect_to_client(pending, flow_id, error="access_denied")

    try:
        user_id, workos_session_id = _authenticate_with_workos(
            code, _workos_pkce_verifier(pending["nonce"])
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
        log.warning("oauth: WorkOS code exchange failed: {}", type(exc).__name__)
        return _redirect_to_client(pending, flow_id, error="server_error")

    if user_signed_out_since(user_id, allowed_at):
        # The user signed out while this sign-in was under way.
        return _redirect_to_client(pending, flow_id, error="access_denied")

    our_code = store_authorization_code(
        client_id=pending["client_id"],
        user_id=user_id,
        redirect_uri=pending["redirect_uri"],
        redirect_uri_provided_explicitly=bool(
            pending["redirect_uri_provided_explicitly"]
        ),
        code_challenge=pending["code_challenge"],
        scopes=list(pending.get("scopes") or []),
        resource=pending["resource"],
        workos_session_id=workos_session_id,
    )
    return _redirect_to_client(pending, flow_id, code=our_code)
