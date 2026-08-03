"""Security response headers (OWASP ASVS V14.4) for every HTTP response.

Registered as the outermost middleware in ``api_server/server.py`` so it
stamps *everything* the process emits: router responses, CORS preflights, the
``MCPAuthMiddleware`` 401 challenge, and the FastMCP mount that answers every
path the routers don't claim.

Implemented as pure ASGI rather than :class:`~starlette.middleware.base.BaseHTTPMiddleware`
for the same reason ``MCPAuthMiddleware`` is: headers are rewritten on the
``http.response.start`` message, so the SSE streams FastMCP emits on ``/mcp``
are never buffered.

Content-Security-Policy comes in two flavours. The default is a deny-everything
policy: this host serves JSON to agents plus three small self-contained HTML
documents (the landing page in ``api_server/routes/_landing.html`` and the
Google OAuth success/error pages in ``api_server/routes/google_oauth.py``),
none of which load a script, an image, a font, or an external stylesheet. The
one thing they do need is the landing page's inline ``<style>`` block, which is
allowed by *hash* rather than by ``'unsafe-inline'`` - the hashes are derived
from the template at import time, so they cannot drift from what is served
(``tests/test_security_headers.py`` pins that).

The exception is FastAPI's interactive docs (``/docs``, ``/redoc``, and the
Swagger OAuth2 redirect page). Those are third-party bundles loaded from a CDN
with an inline bootstrap script, so they get a narrower-than-default-src but
necessarily looser policy scoped to just those paths.
"""

import base64
import hashlib
import re
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The HTML this server renders itself. Only ``_landing.html`` carries a
# `<style>` element today; the OAuth pages are unstyled markup.
_OWN_HTML_TEMPLATES = (Path(__file__).resolve().parent.parent / "routes",)

_STYLE_ELEMENT_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)

# Paths serving FastAPI's Swagger/ReDoc bundles. Matched on the path segment
# (never a bare prefix) so ``/docsearch`` is not handed the looser policy.
_DOCS_ROOTS = ("/docs", "/redoc")

_CDN = "https://cdn.jsdelivr.net"
_FASTAPI_FAVICON_HOST = "https://fastapi.tiangolo.com"
_GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"
_GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"


def _inline_style_hashes() -> tuple[str, ...]:
    """CSP ``sha256-`` source expressions for every inline style we serve.

    Read from the HTML templates on disk so the policy is generated from the
    same bytes the response body carries. A template whose ``<style>`` content
    is substituted at render time would break this - none are today, and the
    test suite asserts the hash matches the live ``/`` response.
    """
    hashes: list[str] = []
    for directory in _OWN_HTML_TEMPLATES:
        for template in sorted(directory.glob("*.html")):
            markup = template.read_text(encoding="utf-8")
            for block in _STYLE_ELEMENT_RE.findall(markup):
                digest = hashlib.sha256(block.encode("utf-8")).digest()
                expression = f"'sha256-{base64.b64encode(digest).decode('ascii')}'"
                if expression not in hashes:
                    hashes.append(expression)
    return tuple(hashes)


def _default_csp() -> str:
    """Deny-by-default policy for the API and this server's own HTML."""
    style_src = " ".join(("'self'", *_inline_style_hashes()))
    return "; ".join(
        (
            "default-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
            "script-src 'none'",
            "object-src 'none'",
            f"style-src {style_src}",
            # Browsers request /favicon.ico unprompted; without img-src that
            # fetch is a console error on every page view.
            "img-src 'self' data:",
            "connect-src 'self'",
        )
    )


# Computed once at import: the templates are read from disk, and re-hashing
# them per request would put a file read in the hot path of every response.
_DEFAULT_CSP = _default_csp()

_DOCS_CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        # Swagger UI and ReDoc ship as CDN bundles booted by an inline script.
        # 'unsafe-inline' is confined to these three documentation paths and
        # never reaches an endpoint that renders user or attacker input.
        f"script-src 'self' 'unsafe-inline' {_CDN}",
        f"style-src 'self' 'unsafe-inline' {_CDN} {_GOOGLE_FONTS_CSS}",
        f"font-src 'self' data: {_GOOGLE_FONTS_FILES}",
        f"img-src 'self' data: {_CDN} {_FASTAPI_FAVICON_HOST}",
        # ReDoc renders the spec in a blob-backed web worker.
        "worker-src 'self' blob:",
        "connect-src 'self'",
        "object-src 'none'",
    )
)

# Two years is the HSTS preload floor; `includeSubDomains` is safe here because
# the deployment terminates TLS for the whole apex it is served from.
_HSTS = "max-age=63072000; includeSubDomains"

# Features this server has no use for. Denying them outright means an injected
# document (or a compromised CDN bundle on /docs) cannot reach the camera,
# microphone, geolocation, or the WebAuthn credential store.
_PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "display-capture",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "publickey-credentials-get",
        "screen-wake-lock",
        "usb",
        "xr-spatial-tracking",
    )
)

STATIC_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("strict-transport-security", _HSTS),
    ("x-content-type-options", "nosniff"),
    # Redundant with `frame-ancestors 'none'` for modern browsers, and required
    # by the older ones (and by every DAST scanner) that don't read CSP.
    ("x-frame-options", "DENY"),
    # The Google OAuth callback carries `code` and `state` in its query string;
    # `no-referrer` is what keeps those out of any outbound Referer header.
    ("referrer-policy", "no-referrer"),
    ("permissions-policy", _PERMISSIONS_POLICY),
    ("x-permitted-cross-domain-policies", "none"),
)


def is_docs_path(path: str) -> bool:
    """True for FastAPI's Swagger/ReDoc documents and their sub-paths."""
    return any(path == root or path.startswith(f"{root}/") for root in _DOCS_ROOTS)


def content_security_policy(path: str) -> str:
    """The CSP this server serves for *path*."""
    return _DOCS_CSP if is_docs_path(path) else _DEFAULT_CSP


class SecurityHeadersMiddleware:
    """Attach the ASVS V14.4 response headers to every HTTP response.

    Existing header values are never overwritten: a route that deliberately
    sets its own (say, a permissive ``Content-Security-Policy`` for an embedded
    document) stays in control of its response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        policy = content_security_policy(scope.get("path", ""))

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in STATIC_SECURITY_HEADERS:
                    headers.setdefault(name, value)
                headers.setdefault("content-security-policy", policy)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
