"""Storage for DaySurface's OAuth server: codes, tokens and their lifetimes.

Codes and tokens are random, opaque, and stored only as SHA-256 hashes (see
``db/models/oauth.py``). The provider (``provider.py``) and the WorkOS callback
(``flow.py``) call these; they are synchronous, so async callers run them off
the event loop.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthToken as TokenResponse
from sqlalchemy import select, update

from api_server.auth.authkit_auth import _resolve_scopes, mcp_resource_url
from db.engine import use_db_session
from db.models.oauth import OAuthCode, OAuthSignOut, OAuthToken, lock_user

CODE_LIFETIME = timedelta(minutes=5)
ACCESS_TOKEN_LIFETIME = timedelta(hours=1)
REFRESH_TOKEN_LIFETIME = timedelta(days=30)
# Absolute cap on one grant: rotation never extends a family past this.
FAMILY_LIFETIME = timedelta(days=90)
_CODE_PREFIX = "dsc_"
_ACCESS_PREFIX = "dsa_"
_REFRESH_PREFIX = "dsr_"


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    # SQLite (tests) returns naive datetimes; Postgres returns aware ones.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _split_scopes(scopes: str) -> list[str]:
    return scopes.split() if scopes else []


def _signed_out_since(session, user_id: str, moment: datetime) -> bool:
    signed_out_at = session.scalar(
        select(OAuthSignOut.signed_out_at).where(OAuthSignOut.user_id == user_id)
    )
    return signed_out_at is not None and _as_utc(signed_out_at) >= _as_utc(moment)


def _widens_access(original: list[str], requested: list[str]) -> bool:
    """Whether a refresh asking for ``requested`` would grant more than ``original``.

    A token with no DaySurface scopes means full access (see
    ``authkit_auth._resolve_scopes``), so dropping the last DaySurface scope on
    refresh would turn a restricted token into an unrestricted one.
    """
    before = _resolve_scopes(" ".join(original))
    after = _resolve_scopes(" ".join(requested))
    if after is None:
        return True
    if before == ["*"]:
        return False
    return after == ["*"] or not set(after) <= set(before or [])


def resource_matches(resource: str | None) -> bool:
    """Whether ``resource`` names this MCP server (RFC 8707 audience)."""
    if resource is None:
        return False
    canonical = mcp_resource_url()
    return resource.rstrip("/") == canonical.rstrip("/")


# ---------------------------------------------------------------------------
# Storage (synchronous; the provider calls these off the event loop)
# ---------------------------------------------------------------------------


def store_authorization_code(
    *,
    client_id: str,
    user_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    scopes: list[str],
    resource: str,
    workos_session_id: str | None,
    started_at: datetime,
) -> str | None:
    """Persist a new authorization code and return its plaintext value.

    Returns ``None``, storing nothing, when the user signed out at or after
    ``started_at`` (the moment they allowed this sign-in).
    """
    code = _new_secret(_CODE_PREFIX)
    with use_db_session() as session:
        lock_user(session, user_id)
        if _signed_out_since(session, user_id, started_at):
            return None
        session.add(
            OAuthCode(
                code_hash=token_hash(code),
                client_id=client_id,
                user_id=user_id,
                redirect_uri=redirect_uri,
                redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
                code_challenge=code_challenge,
                scopes=" ".join(scopes),
                resource=resource,
                workos_session_id=workos_session_id,
                expires_at=_now() + CODE_LIFETIME,
            )
        )
        session.commit()
    return code


def _issue_token_pair(
    session,
    *,
    family_id: str,
    user_id: str,
    client_id: str,
    scopes: list[str],
    resource: str | None,
    workos_session_id: str | None,
    family_expires_at: datetime,
) -> TokenResponse:
    access = _new_secret(_ACCESS_PREFIX)
    refresh = _new_secret(_REFRESH_PREFIX)
    now = _now()
    family_expires_at = _as_utc(family_expires_at)
    access_expires_at = min(now + ACCESS_TOKEN_LIFETIME, family_expires_at)
    common = {
        "family_id": family_id,
        "user_id": user_id,
        "client_id": client_id,
        "scopes": " ".join(scopes),
        "resource": resource,
        "workos_session_id": workos_session_id,
        "family_expires_at": family_expires_at,
    }
    session.add(
        OAuthToken(
            token_hash=token_hash(access),
            kind="access",
            expires_at=access_expires_at,
            **common,
        )
    )
    session.add(
        OAuthToken(
            token_hash=token_hash(refresh),
            kind="refresh",
            expires_at=min(now + REFRESH_TOKEN_LIFETIME, family_expires_at),
            **common,
        )
    )
    return TokenResponse(
        access_token=access,
        token_type="Bearer",
        expires_in=max(int((access_expires_at - now).total_seconds()), 0),
        refresh_token=refresh,
        scope=" ".join(scopes) or None,
    )


def _revoke_family(session, family_id: str) -> None:
    session.execute(
        update(OAuthToken)
        .where(OAuthToken.family_id == family_id, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def lookup_access_token(token: str) -> AccessToken | None:
    """Return the live access token for ``token``, or ``None``.

    Used by the /mcp middleware: unknown, revoked, expired or wrong-audience
    tokens all read as ``None`` (401).
    """
    if not token.startswith(_ACCESS_PREFIX):
        return None
    with use_db_session() as session:
        row = session.get(OAuthToken, token_hash(token))
        if (
            row is None
            or row.kind != "access"
            or row.revoked_at is not None
            or _as_utc(row.expires_at) <= _now()
            or not resource_matches(row.resource)
        ):
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=_split_scopes(row.scopes),
            expires_at=int(_as_utc(row.expires_at).timestamp()),
            resource=row.resource,
            subject=row.user_id,
        )
