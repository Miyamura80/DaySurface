"""Signed, short-lived values carried through the browser during sign-in.

The pending authorization request travels from ``/authorize`` to the consent
page, and then to WorkOS and back as ``state``. Both legs are signed with
``SESSION_SECRET_KEY`` (HMAC, via itsdangerous) under separate salts, so one
cannot stand in for the other, and both expire after ten minutes. They carry no
secrets: client id, redirect URI, PKCE challenge, the client's own ``state``,
scopes and resource.
"""

from datetime import UTC, datetime
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer

from common import global_config

MAX_AGE_SECONDS = 600
_PENDING_SALT = "daysurface.oauth.pending-authorization"
_WORKOS_STATE_SALT = "daysurface.oauth.workos-state"


class InvalidSignedValueError(ValueError):
    """The value was tampered with, expired, or signed for another purpose."""


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(global_config.SESSION_SECRET_KEY, salt=salt)


def _load(salt: str, value: str) -> tuple[dict[str, Any], datetime]:
    try:
        payload, signed_at = _serializer(salt).loads(
            value, max_age=MAX_AGE_SECONDS, return_timestamp=True
        )
    except BadSignature as exc:  # SignatureExpired is a subclass
        raise InvalidSignedValueError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise InvalidSignedValueError("unexpected payload")
    if signed_at.tzinfo is None:
        signed_at = signed_at.replace(tzinfo=UTC)
    return payload, signed_at


def sign_pending_authorization(payload: dict[str, Any]) -> str:
    return _serializer(_PENDING_SALT).dumps(payload)


def load_pending_authorization(value: str) -> dict[str, Any]:
    return _load(_PENDING_SALT, value)[0]


def sign_workos_state(payload: dict[str, Any]) -> str:
    return _serializer(_WORKOS_STATE_SALT).dumps(payload)


def load_workos_state(value: str) -> tuple[dict[str, Any], datetime]:
    """The WorkOS state and when it was signed (the moment the user chose Allow)."""
    return _load(_WORKOS_STATE_SALT, value)
