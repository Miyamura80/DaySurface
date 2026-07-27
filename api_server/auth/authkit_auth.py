"""AuthKit (WorkOS) OAuth 2.1 access-token verification for the /mcp transport.

Per the MCP authorization spec (2025-11-25) the MCP server is an OAuth 2.1
*resource server*: AuthKit acts as the authorization server (client
registration via CIMD/DCR, PKCE, consent) and issues RS256 access tokens
audience-bound to this server's canonical resource URI (RFC 8707). This module
verifies signature, issuer, and audience of those tokens.

Enabled by setting ``WORKOS_AUTHKIT_DOMAIN`` (the AuthKit issuer URL, e.g.
``https://your-env.authkit.app``). ``MCP_PUBLIC_URL`` must match the resource
indicator configured in the WorkOS dashboard.
"""

from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient

from common import global_config

_jwks_clients: dict[str, PyJWKClient] = {}


def authkit_domain() -> str | None:
    """Return the configured AuthKit issuer URL without a trailing slash."""
    domain = global_config.WORKOS_AUTHKIT_DOMAIN
    return domain.rstrip("/") if domain else None


def mcp_resource_url() -> str:
    """Canonical RFC 8707 resource URI of the /mcp endpoint.

    Tokens are audience-bound to this exact string, so in any deployed
    environment ``MCP_PUBLIC_URL`` must be set to the public URL clients
    connect to (no trailing slash, per the MCP spec's canonical-URI guidance).
    """
    configured = global_config.MCP_PUBLIC_URL
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{global_config.server.port}/mcp"


def resource_metadata_url() -> str:
    """RFC 9728 path-form well-known URL for the resource above."""
    parts = urlsplit(mcp_resource_url())
    origin = f"{parts.scheme}://{parts.netloc}"
    return f"{origin}/.well-known/oauth-protected-resource{parts.path}"


def _get_jwks_client(domain: str) -> PyJWKClient:
    if domain not in _jwks_clients:
        _jwks_clients[domain] = PyJWKClient(f"{domain}/oauth2/jwks")
    return _jwks_clients[domain]


class AuthKitUser:
    """Minimal representation of a user verified from an AuthKit access token."""

    def __init__(
        self, user_id: str, email: str | None = None, scopes: list[str] | None = None
    ):
        self.user_id = user_id
        self.email = email
        self.scopes = scopes


def verify_authkit_token(token: str) -> AuthKitUser | None:
    """Verify an AuthKit-issued access token; return the user or ``None``."""
    domain = authkit_domain()
    if not domain:
        return None

    try:
        signing_key = _get_jwks_client(domain).get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=mcp_resource_url(),
            issuer=[domain, domain + "/"],
        )
    except jwt.PyJWTError:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    scopes = _resolve_scopes(payload.get("scope"))
    if scopes is None:
        # Malformed scope claim: reject the token instead of erroring.
        return None

    return AuthKitUser(user_id=user_id, email=payload.get("email"), scopes=scopes)


def _resolve_scopes(raw: object) -> list[str] | None:
    """Map an AuthKit access-token ``scope`` claim onto this server's scopes.

    AuthKit access tokens carry OIDC *identity* scopes (``openid``, ``profile``,
    ``email``, ``offline_access``) that are orthogonal to this server's
    ``services:*`` *authorization* namespace. We honor only scopes that belong
    to our namespace; when the token carries none of them - the normal case for
    an interactive OAuth login, where WorkOS issues only identity scopes - the
    user consented to the whole resource and gets full access (``["*"]``),
    mirroring how ``unified_auth`` treats interactive first-party JWT users.
    Genuine down-scoping still works: a token that *does* carry our scopes
    (e.g. WorkOS configured to issue ``services:read``) is restricted to them.

    Fails closed when the token references our namespace but no value resolves
    to a real scope (a typo/misconfiguration such as ``services:exceute``):
    returns ``None`` so the caller rejects the token instead of silently
    upgrading a botched down-scoping to full access. Also returns ``None`` for a
    malformed (non-string, non-list) claim.
    """
    if raw is None:
        return ["*"]
    if isinstance(raw, str):
        requested = raw.split()
    elif isinstance(raw, list):
        requested = [str(s) for s in raw]
    else:
        return None

    # Lazy import: api_server.auth.scopes pulls in the FastAPI dependency graph,
    # and this module is imported early by middleware.
    from api_server.auth.scopes import ALL_SCOPES  # noqa: PLC0415

    known_prefixes = {s.split(":")[0] for s in ALL_SCOPES if ":" in s}

    def _is_authz_scope(s: str) -> bool:
        if s == "*" or s in ALL_SCOPES:
            return True
        return (
            s.endswith(":*") and s.count(":") == 1 and s.split(":")[0] in known_prefixes
        )

    def _targets_authz_namespace(s: str) -> bool:
        # Looks like it addresses our namespace (``<known>:<anything>``) even if
        # it is not a valid scope - used to detect misconfigured down-scoping.
        # Match any colon count so ``services:foo:bar`` can't slip past.
        return s == "*" or (":" in s and s.split(":", 1)[0] in known_prefixes)

    granted = [s for s in requested if _is_authz_scope(s)]
    if granted:
        return granted
    if any(_targets_authz_namespace(s) for s in requested):
        return None
    return ["*"]
