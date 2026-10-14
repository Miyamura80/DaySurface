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


_ALLOWED_AT = "allowed_at"


def _load(salt: str, value: str) -> dict[str, Any]:
    try:
        payload = _serializer(salt).loads(value, max_age=MAX_AGE_SECONDS)
    except BadSignature as exc:  # SignatureExpired is a subclass
        raise InvalidSignedValueError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise InvalidSignedValueError("unexpected payload")
    return payload


def sign_pending_authorization(payload: dict[str, Any]) -> str:
    return _serializer(_PENDING_SALT).dumps(payload)


def load_pending_authorization(value: str) -> dict[str, Any]:
    return _load(_PENDING_SALT, value)


def sign_workos_state(payload: dict[str, Any]) -> str:
    """Sign the WorkOS state, stamped with the moment the user chose Allow.

    The stamp keeps microseconds (the signature's own timestamp is whole
    seconds), so a sign-in started just after a sign-out is not mistaken for
    one that began before it.
    """
    stamped = {**payload, _ALLOWED_AT: datetime.now(UTC).isoformat()}
    return _serializer(_WORKOS_STATE_SALT).dumps(stamped)


def load_workos_state(value: str) -> tuple[dict[str, Any], datetime]:
    """The WorkOS state and when the user chose Allow."""
    payload = _load(_WORKOS_STATE_SALT, value)
    try:
        allowed_at = datetime.fromisoformat(payload[_ALLOWED_AT])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSignedValueError("state has no valid allowed_at") from exc
    if allowed_at.tzinfo is None:
        raise InvalidSignedValueError("allowed_at must carry a time zone")
    return payload, allowed_at
