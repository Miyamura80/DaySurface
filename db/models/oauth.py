"""OAuth authorization codes and tokens issued by DaySurface's own OAuth server.

DaySurface is the OAuth 2.1 authorization server for MCP clients (see
``api_server/oauth/``). Every code and token is stored only as a SHA-256 hash of
its value, so a database read cannot replay one.

A *family* is one authorization grant: the tokens issued from one authorization
code, plus every refresh-token rotation after it. Refresh tokens are single-use;
presenting one that was already used revokes the whole family (OAuth 2.1
section 4.3.1 refresh-token reuse detection).
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class OAuthCode(Base):
    __tablename__ = "oauth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The WorkOS session created by the upstream sign-in, so sign-out can end it.
    workos_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set when the code is exchanged; a second exchange is refused.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # access | refresh
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    workos_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Absolute end of the grant: refreshing never extends a family past this,
    # however often it rotates.
    family_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set on logout, on revocation, and when a refresh token is rotated (used).
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class OAuthSignOut(Base):
    """When each user last signed out.

    Revoking tokens is not enough on its own: a sign-in already under way (the
    browser is on its way back from WorkOS) or an issued but unexchanged code
    would otherwise still produce tokens afterwards. Anything that began before
    ``signed_out_at`` is refused.
    """

    __tablename__ = "oauth_sign_outs"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    signed_out_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
