"""WorkOS JWT verification.

Verifies RS256 JWTs issued by WorkOS using their published JWKS.
Supports a test-mode bypass for local development.
"""

import json

import jwt
from jwt import PyJWKClient

from common import global_config

_jwks_client: PyJWKClient | None = None

WORKOS_ISSUERS = [
    "https://api.workos.com",
    "https://api.workos.com/",
]


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client  # noqa: PLW0603
    if _jwks_client is None:
        _jwks_client = PyJWKClient("https://api.workos.com/sso/jwks/")
    return _jwks_client


class WorkOSUser:
    """Minimal representation of a verified WorkOS user."""

    def __init__(self, user_id: str, email: str | None = None):
        self.user_id = user_id
        self.email = email


def verify_workos_token(token: str) -> WorkOSUser | None:
    """Verify a WorkOS JWT and return a ``WorkOSUser`` or ``None``."""
    client_id = global_config.WORKOS_CLIENT_ID
    if not client_id:
        return None

    # Test-mode bypass: only allowed in non-production environments
    if token.startswith("{") and global_config.DEV_ENV in ("local", "dev"):
        try:
            payload = json.loads(token)
            return WorkOSUser(
                user_id=payload["sub"],
                email=payload.get("email"),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=WORKOS_ISSUERS,
        )
    except jwt.PyJWTError:
        return None

    user = WorkOSUser(user_id=payload["sub"], email=payload.get("email"))
    user = _hydrate_user_from_workos_api(user) or user
    return user


def _hydrate_user_from_workos_api(user: WorkOSUser) -> WorkOSUser | None:
    """Attempt to enrich user info from the WorkOS management API."""
    api_key = global_config.WORKOS_API_KEY
    if not api_key or user.email:
        return user
    # WorkOS API hydration would go here; skip if no API key.
    return user
