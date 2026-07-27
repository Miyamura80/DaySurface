"""API key authentication.

Keys use a ``sk_`` prefix and are stored as SHA-256 hashes.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from db.models.api_keys import APIKey
from db.utils import ensure_profile_exists

PREFIX = "sk_"
KEY_BYTES = 32


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(
    session: Session,
    *,
    user_id: str,
    name: str = "Default",
    expires_in_days: int | None = None,
    email: str | None = None,
    scopes: list[str] | None = None,
) -> tuple[str, APIKey]:
    """Create a new API key and return ``(raw_key, db_row)``."""
    ensure_profile_exists(session, user_id=user_id, email=email)

    raw_key = PREFIX + secrets.token_hex(KEY_BYTES)
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[: len(PREFIX) + 8]

    expires_at = None
    if expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

    row = APIKey(
        user_id=user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=name,
        scopes=scopes,
        expires_at=expires_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return raw_key, row


def validate_api_key(session: Session, raw_key: str) -> APIKey | None:
    """Validate a raw API key and return the DB row (or ``None``)."""
    if not raw_key.startswith(PREFIX):
        return None
    key_hash = _hash_key(raw_key)
    row = session.query(APIKey).filter_by(key_hash=key_hash).first()
    if row is None:
        return None
    if row.revoked:
        return None
    if row.expires_at:
        expires = (
            row.expires_at
            if row.expires_at.tzinfo
            else row.expires_at.replace(tzinfo=UTC)
        )
        if expires < datetime.now(UTC):
            return None

    row.last_used_at = datetime.now(UTC)
    session.commit()
    return row


def get_user_id_for_key_hash(session: Session, key_hash: str) -> str | None:
    """Return the user_id for a valid (non-revoked, non-expired) API key hash.

    Shared by auth validation and rate-limit tier lookup to keep validity
    checks in a single place.
    """
    row = (
        session.query(APIKey.user_id)
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.revoked.is_(False),
            or_(
                APIKey.expires_at.is_(None),
                APIKey.expires_at > datetime.now(UTC),
            ),
        )
        .first()
    )
    return row.user_id if row else None


def revoke_api_key(session: Session, *, key_id: int, user_id: str) -> bool:
    """Revoke an API key. Returns ``True`` if the key was found and revoked."""
    row = session.query(APIKey).filter_by(id=key_id, user_id=user_id).first()
    if row is None:
        return False
    row.revoked = True
    session.commit()
    return True
