"""Security response headers (OWASP ASVS V14.4) for every HTTP response.

Registered as the outermost middleware in ``api_server/server.py`` so it
stamps *everything* the process emits: router responses, CORS preflights, the
``MCPAuthMiddleware`` 401 challenge, and the FastMCP mount that answers every
path the routers don't claim.

Implemented as pure ASGI rather than :class:`~starlette.middleware.base.BaseHTTPMiddleware`
for the same reason ``MCPAuthMiddleware`` is: headers are rewritten on the
``http.response.start`` message, so the SSE streams FastMCP emits on ``/mcp``
are never buffered.

The default Content-Security-Policy denies everything. This host serves JSON to
agents plus a few small self-contained HTML documents, none of which load a
script, an image, a font, or an external stylesheet. A route that legitimately
needs more sets its own ``Content-Security-Policy`` on the response and the
middleware leaves it alone (every header here is applied with ``setdefault``) -
that is how ``api_server/routes/index.py`` allows the landing page's inline
``<style>`` by hash, computed from the exact markup it is about to send.
Deriving the exception where the document is rendered keeps it scoped to that
one response instead of widening ``style-src`` on every response the server
emits, and makes "the hash cannot drift" true by construction.

The one exception the middleware owns is FastAPI's interactive docs. Those are
third-party bundles loaded from a CDN with an inline bootstrap script, so they
get a necessarily looser policy - scoped by exact path match to the URLs the
FastAPI app actually serves them on, which ``server.py`` passes in rather than
this module hardcoding (and later desyncing from) the defaults.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CDN = "https://cdn.jsdelivr.net"
_FASTAPI_FAVICON_HOST = "https://fastapi.tiangolo.com"
_GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"
_GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"

DEFAULT_CSP = "; ".join(
    (
        "default-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "script-src 'none'",
        "object-src 'none'",
        "style-src 'self'",
        # Browsers request /favicon.ico unprompted; without img-src that fetch
        # is a console error on every page view.
        "img-src 'self' data:",
        "connect-src 'self'",
    )
)

DOCS_CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        # Swagger UI and ReDoc ship as CDN bundles booted by an inline script.
        # 'unsafe-inline' is confined to the documentation paths and never
        # reaches an endpoint that renders user or attacker input.
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


class SecurityHeadersMiddleware:
    """Attach the ASVS V14.4 response headers to every HTTP response.

    Existing header values are never overwritten: a route that deliberately
    sets its own (say, a ``Content-Security-Policy`` carrying a hash for its
    own inline style) stays in control of its response.

    ``docs_paths`` is the exact set of URLs serving FastAPI's Swagger/ReDoc
    bundles. It is injected rather than hardcoded so it tracks whatever the app
    is actually configured with, and matched exactly rather than by prefix so
    an unmatched path under ``/docs/`` - which falls through to the MCP mount's
    404 handler - is not handed the looser policy on the strength of its URL
    shape alone.
    """

    def __init__(self, app: ASGIApp, docs_paths: frozenset[str]) -> None:
        self.app = app
        self.docs_paths = docs_paths

    def _csp_for(self, scope: Scope) -> str:
        """Pick the policy for this request's path.

        ``app.docs_url`` and friends are *root_path-relative*, but this
        middleware is the outermost layer and sees the full path: mounted under
        ``root_path="/api"``, Swagger arrives as ``/api/docs`` while
        ``docs_paths`` holds ``/docs``. Strip the prefix before comparing, or
        the docs bundle silently gets ``script-src 'none'`` and renders blank.
        """
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root) :] or "/"
        return DOCS_CSP if path in self.docs_paths else DEFAULT_CSP

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        policy = self._csp_for(scope)

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in STATIC_SECURITY_HEADERS:
                    headers.setdefault(name, value)
                headers.setdefault("content-security-policy", policy)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
