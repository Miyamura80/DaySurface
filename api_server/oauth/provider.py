"""DaySurface's own OAuth 2.1 authorization server for MCP clients.

DaySurface issues, stores and revokes the tokens MCP clients use, so logging out
can invalidate them (CASA 2.2.1). WorkOS AuthKit still performs the sign-in: the
authorize step sends the user to a DaySurface consent page and then to WorkOS,
and the WorkOS callback (``api_server/oauth/flow.py``) mints the authorization
code this provider later exchanges.

Token handling, per OAuth 2.1 and the MCP authorization spec (2026-07-28):

* Codes and tokens are random, opaque, and stored only as SHA-256 hashes.
* Access tokens live for one hour and are bound to this server's canonical MCP
  URL (RFC 8707); a request for any other resource is refused.
* Refresh tokens are single-use. Each refresh rotates to a new token in the same
  family; presenting a used refresh token revokes the whole family. A family
  ends 90 days after sign-in however often it rotates, and a refresh can never
  widen what the token may do.
* A code can be exchanged once; a replayed code revokes what it issued.
* After a user signs out, no code or sign-in that began earlier yields tokens.

Everything that issues a code or tokens, and sign-out itself, first takes a
per-user lock (``db.models.oauth.lock_user``), so a sign-out cannot interleave with an
exchange or refresh and leave a token it did not see.
"""

import re

import anyio
from loguru import logger as log
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse
from pydantic import AnyUrl
from sqlalchemy import select, update

from api_server.auth.authkit_auth import mcp_resource_url
from api_server.oauth import cimd, signing
from api_server.oauth.storage import (
    FAMILY_LIFETIME,
    _as_utc,
    _issue_token_pair,
    _now,
    _revoke_family,
    _signed_out_since,
    _split_scopes,
    _widens_access,
    lookup_access_token,
    resource_matches,
    token_hash,
)
from db.engine import use_db_session
from db.models.oauth import OAuthCode, OAuthToken, lock_user

# RFC 7636: an S256 challenge is a SHA-256 digest, base64url without padding.
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_REFRESH_PREFIX = "dsr_"


# ---------------------------------------------------------------------------
# SDK provider
# ---------------------------------------------------------------------------


class DaySurfaceOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Plugs DaySurface's storage and WorkOS sign-in into the MCP SDK's OAuth routes."""

    def __init__(self, issuer_url: str) -> None:
        self.issuer_url = issuer_url.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        try:
            return await anyio.to_thread.run_sync(cimd.resolve_client, client_id)
        except cimd.ClientMetadataError as exc:
            log.warning("oauth: rejected client metadata document: {}", exc)
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Dynamic Client Registration is deprecated in the MCP spec; DaySurface
        # only accepts Client ID Metadata Documents.
        raise RegistrationError(
            error="invalid_client_metadata",
            error_description="Use a Client ID Metadata Document URL as client_id.",
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Bind the token to this server (RFC 8707). Clients must send resource;
        # when one is absent, the token is still bound to this server.
        resource = params.resource or mcp_resource_url()
        if not resource_matches(resource):
            raise AuthorizeError(
                error="invalid_request",
                error_description="resource must be this MCP server's URL",
            )
        if not _CODE_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError(
                error="invalid_request",
                error_description="code_challenge must be an RFC 7636 S256 challenge",
            )
        pending = signing.sign_pending_authorization(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [],
                "resource": resource,
            }
        )
        # Consent comes first: DaySurface must show the user which client is
        # asking before forwarding to WorkOS (MCP spec, confused deputy).
        return f"{self.issuer_url}/oauth/consent?request={pending}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        def _load() -> AuthorizationCode | None:
            with use_db_session() as session:
                row = session.get(OAuthCode, token_hash(authorization_code))
                if row is None or row.client_id != client.client_id:
                    return None
                if row.used_at is not None:
                    # A replayed code: revoke what the first exchange issued.
                    lock_user(session, row.user_id)
                    _revoke_family(session, row.code_hash)
                    session.commit()
                    return None
                return AuthorizationCode(
                    code=authorization_code,
                    scopes=_split_scopes(row.scopes),
                    expires_at=_as_utc(row.expires_at).timestamp(),
                    client_id=row.client_id,
                    code_challenge=row.code_challenge,
                    redirect_uri=AnyUrl(row.redirect_uri),
                    redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
                    resource=row.resource,
                    subject=row.user_id,
                )

        return await anyio.to_thread.run_sync(_load)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> TokenResponse:
        def _exchange() -> TokenResponse:
            code_hash = token_hash(authorization_code.code)
            with use_db_session() as session:
                user_id = session.scalar(
                    select(OAuthCode.user_id).where(OAuthCode.code_hash == code_hash)
                )
                if user_id is None:
                    raise TokenError(
                        error="invalid_grant", error_description="unknown code"
                    )
                lock_user(session, user_id)
                # Single use, atomically: only the first exchange flips used_at.
                claimed = session.execute(
                    update(OAuthCode)
                    .where(
                        OAuthCode.code_hash == code_hash, OAuthCode.used_at.is_(None)
                    )
                    .values(used_at=_now())
                )
                if not claimed.rowcount:  # ty: ignore[unresolved-attribute]
                    # Lost the race to a concurrent exchange of the same code:
                    # a replay, so revoke what the winner issued.
                    _revoke_family(session, code_hash)
                    session.commit()
                    raise TokenError(
                        error="invalid_grant",
                        error_description="authorization code was already used",
                    )
                row = session.get(OAuthCode, code_hash)
                if row is None:  # claimed above in this transaction; defensive
                    session.rollback()
                    raise TokenError(
                        error="invalid_grant", error_description="unknown code"
                    )
                if _signed_out_since(session, row.user_id, row.created_at):
                    session.commit()  # keep the code spent
                    raise TokenError(
                        error="invalid_grant",
                        error_description="the user signed out after this code was issued",
                    )
                # The family id is the code hash, so a replayed code can find
                # and revoke exactly the tokens this exchange issues.
                response = _issue_token_pair(
                    session,
                    family_id=code_hash,
                    user_id=row.user_id,
                    client_id=row.client_id,
                    scopes=_split_scopes(row.scopes),
                    resource=row.resource,
                    workos_session_id=row.workos_session_id,
                    family_expires_at=_now() + FAMILY_LIFETIME,
                )
                session.commit()
                return response

        return await anyio.to_thread.run_sync(_exchange)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        if not refresh_token.startswith(_REFRESH_PREFIX):
            return None

        def _load() -> RefreshToken | None:
            with use_db_session() as session:
                row = session.get(OAuthToken, token_hash(refresh_token))
                if (
                    row is None
                    or row.kind != "refresh"
                    or row.client_id != client.client_id
                ):
                    return None
                if row.revoked_at is not None:
                    # Reuse of a rotated (or revoked) refresh token: treat the
                    # family as compromised. Under the lock, so a refresh of the
                    # same family running now cannot slip a token past this.
                    lock_user(session, row.user_id)
                    _revoke_family(session, row.family_id)
                    session.commit()
                    log.warning("oauth: refresh token reuse; revoked its token family")
                    return None
                return RefreshToken(
                    token=refresh_token,
                    client_id=row.client_id,
                    scopes=_split_scopes(row.scopes),
                    expires_at=int(_as_utc(row.expires_at).timestamp()),
                    resource=row.resource,
                    subject=row.user_id,
                )

        return await anyio.to_thread.run_sync(_load)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> TokenResponse:
        def _exchange() -> TokenResponse:
            old_hash = token_hash(refresh_token.token)
            with use_db_session() as session:
                user_id = session.scalar(
                    select(OAuthToken.user_id).where(OAuthToken.token_hash == old_hash)
                )
                if user_id is None:
                    raise TokenError(
                        error="invalid_grant", error_description="unknown refresh token"
                    )
                lock_user(session, user_id)
                # Rotate atomically: only one caller can consume this token.
                consumed = session.execute(
                    update(OAuthToken)
                    .where(
                        OAuthToken.token_hash == old_hash,
                        OAuthToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=_now())
                )
                row = session.get(OAuthToken, old_hash)
                if not consumed.rowcount or row is None:  # ty: ignore[unresolved-attribute]
                    if row is not None:
                        _revoke_family(session, row.family_id)
                    session.commit()
                    raise TokenError(
                        error="invalid_grant",
                        error_description="refresh token was already used",
                    )
                original = _split_scopes(row.scopes)
                requested = scopes or original
                if _widens_access(original, requested):
                    session.rollback()  # leave the refresh token unspent
                    raise TokenError(
                        error="invalid_scope",
                        error_description="a refresh cannot widen the token's access",
                    )
                response = _issue_token_pair(
                    session,
                    family_id=row.family_id,
                    user_id=row.user_id,
                    client_id=row.client_id,
                    scopes=requested,
                    resource=row.resource,
                    workos_session_id=row.workos_session_id,
                    family_expires_at=row.family_expires_at,
                )
                session.commit()
                return response

        return await anyio.to_thread.run_sync(_exchange)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await anyio.to_thread.run_sync(lookup_access_token, token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def _revoke() -> None:
            with use_db_session() as session:
                row = session.get(OAuthToken, token_hash(token.token))
                if row is not None:
                    # Revoke both the access and the refresh token (RFC 7009).
                    lock_user(session, row.user_id)
                    _revoke_family(session, row.family_id)
                    session.commit()

        await anyio.to_thread.run_sync(_revoke)
