"""Database helper utilities."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models.profiles import Profile


def user_uuid_from_str(value: str) -> uuid.UUID:
    """Parse *value* as a UUID; fall back to a deterministic uuid5."""
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, value)


def ensure_profile_exists(
    session: Session,
    *,
    user_id: str,
    email: str | None = None,
    username: str | None = None,
) -> Profile:
    """Return the existing profile or create a minimal one."""
    profile = session.query(Profile).filter_by(user_id=user_id).first()
    if profile is not None:
        return profile

    try:
        profile = Profile(user_id=user_id, email=email, username=username)
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile
    except IntegrityError:
        session.rollback()
        return session.query(Profile).filter_by(user_id=user_id).one()
