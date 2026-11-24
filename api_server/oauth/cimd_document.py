"""Client ID Metadata Documents: the URL shape, the fetch and the document.

An MCP client such as claude.ai identifies itself with an HTTPS URL as its
``client_id``. That URL serves a JSON document describing the client (its name
and redirect URIs). This module validates the URL, fetches the document from a
pinned target and turns it into an SDK client record, following the MCP
authorization spec (2026-07-28, Client Registration) and
draft-ietf-oauth-client-id-metadata-document-00. Caching, concurrency and the
deadline live in :mod:`api_server.oauth.cimd`, the only caller.

The fetch targets a client-chosen URL, so it is treated as hostile: it is sent
with :func:`services._ssrf_guard.build_async_client` (no environment proxy, no
redirects) to a target the caller pinned with
:func:`services._ssrf_guard.validate_and_pin_async`, and the body is capped at
``_MAX_DOCUMENT_BYTES`` as sent, with compression refused so the cap cannot be
inflated past.

Failures come in two kinds, which the cache treats differently (RFC 5861
stale-if-error): :class:`TransientFetchError` says nothing about the document
(a 5xx, a network error, a timeout), while any other
:class:`ClientMetadataError` is a definite answer (a 4xx, a document that is
too large, compressed, malformed or names another client).
"""

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, ValidationError

from api_server.auth.scopes import ALL_SCOPES
from services._ssrf_guard import PinnedTarget, build_async_client

_MAX_DOCUMENT_BYTES = 10_000
_MAX_CLIENT_ID_LENGTH = 2000  # oauth_codes.client_id is String(2048)
# Scopes a client may request. The identity scopes are what MCP clients ask for
# when they want refresh tokens and a profile; the rest are DaySurface's own.
# A token always grants full access unless it carries DaySurface scopes (see
# api_server.auth.authkit_auth._resolve_scopes), so this list only gates what a
# request may name, not what it gets.
_REQUESTABLE_SCOPES = sorted(
    {"openid", "profile", "email", "offline_access", *ALL_SCOPES}
)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ClientMetadataError(ValueError):
    """The client_id is not a usable Client ID Metadata Document."""


class TransientFetchError(ClientMetadataError):
    """The fetch failed without an answer about the document (5xx, network, timeout)."""


def is_metadata_document_client_id(client_id: str) -> bool:
    """Whether ``client_id`` has the shape of a CIMD URL.

    The spec requires https and a path component, and forbids a fragment.
    Userinfo, query strings, dot segments and non-ASCII characters are refused
    as well, so one client cannot be addressed by several spellings or shown
    under a look-alike host.
    """
    if not client_id.isascii() or len(client_id) > _MAX_CLIENT_ID_LENGTH:
        return False
    try:
        parts = urlsplit(client_id)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.path not in ("", "/")
        and not parts.fragment
        and not parts.query
        and "@" not in parts.netloc
        and "/./" not in parts.path
        and "/../" not in parts.path
        and not parts.path.endswith(("/.", "/.."))
    )


def is_loopback_redirect(uri: str) -> bool:
    return urlsplit(uri).hostname in _LOOPBACK_HOSTS


def _same_loopback_target(registered: str, requested: str) -> bool:
    """Loopback redirects match on everything but the port (RFC 8252 section 7.3).

    Native apps listen on whatever port the OS hands them, so a registered
    ``http://127.0.0.1/callback`` must accept ``http://127.0.0.1:53817/callback``.
    """
    a, b = urlsplit(registered), urlsplit(requested)
    return (
        a.scheme == b.scheme == "http"
        and a.hostname == b.hostname
        and a.hostname in _LOOPBACK_HOSTS
        and a.path == b.path
        and a.query == b.query
    )


class MetadataDocumentClient(OAuthClientInformationFull):
    """A client from a metadata document, with RFC 8252 loopback port matching."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and self.redirect_uris:
            requested = str(redirect_uri)
            if is_loopback_redirect(requested) and any(
                _same_loopback_target(str(registered), requested)
                for registered in self.redirect_uris
            ):
                return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


def _validate_redirect_uri(uri: object) -> str:
    if not isinstance(uri, str) or not uri.isascii():
        raise ClientMetadataError("redirect_uris must be ASCII strings")
    try:
        parts = urlsplit(uri)
        hostname = parts.hostname
    except ValueError as exc:  # e.g. an unbalanced IPv6 bracket
        raise ClientMetadataError("redirect_uris must be valid URLs") from exc
    if parts.fragment:
        raise ClientMetadataError("redirect_uris must not contain a fragment")
    if parts.scheme == "https" and hostname:
        return uri
    if parts.scheme == "http" and hostname in _LOOPBACK_HOSTS:
        return uri
    raise ClientMetadataError("redirect_uris must use https or a loopback address")


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    body: bytes
    cache_control: str | None


async def fetch_document(target: PinnedTarget, *, timeout: float) -> FetchedDocument:
    """GET the document at a pinned ``target``.

    ``timeout`` bounds each socket operation; the caller bounds the whole fetch
    by cancelling it. Raises :class:`TransientFetchError` or
    :class:`ClientMetadataError` (see the module docstring).
    """
    # Ask for the bytes as stored, so the size cap applies to what is sent.
    headers = {
        **target.headers,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    try:
        async with (
            build_async_client(timeout=timeout) as client,
            client.stream(
                "GET", target.url, headers=headers, extensions=target.extensions
            ) as response,
        ):
            status = response.status_code
            if 500 <= status < 600:
                raise TransientFetchError(
                    f"client metadata fetch returned HTTP {status}"
                )
            if status != 200:
                raise ClientMetadataError(
                    f"client metadata fetch returned HTTP {status}"
                )
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise ClientMetadataError(
                    "client metadata document must not be compressed"
                )
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > _MAX_DOCUMENT_BYTES:
                    raise ClientMetadataError("client metadata document is too large")
            return FetchedDocument(bytes(body), response.headers.get("cache-control"))
    except httpx.TransportError as exc:  # network, protocol and timeout errors
        raise TransientFetchError(
            "client metadata document could not be fetched"
        ) from exc
    except httpx.HTTPError as exc:
        raise ClientMetadataError(
            "client metadata document could not be fetched"
        ) from exc
    except UnicodeError as exc:  # a header or host httpx cannot encode
        raise ClientMetadataError(
            "client metadata document could not be fetched"
        ) from exc


def parse_document(client_id: str, raw: bytes) -> OAuthClientInformationFull:
    """Validate a fetched document and turn it into an SDK client record."""
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientMetadataError("client metadata document is not JSON") from exc
    if not isinstance(doc, dict):
        raise ClientMetadataError("client metadata document must be a JSON object")
    # The document must name itself: this is what binds the URL to the client.
    if doc.get("client_id") != client_id:
        raise ClientMetadataError("client_id in the document does not match its URL")
    redirect_uris = doc.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise ClientMetadataError("client metadata document has no redirect_uris")
    validated = [_validate_redirect_uri(uri) for uri in redirect_uris]
    auth_method = doc.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        # Only public clients are supported; a client that expects to
        # authenticate with a key we never verify must not be treated as public.
        raise ClientMetadataError(
            "only public clients (token_endpoint_auth_method=none) are supported"
        )
    name = doc.get("client_name")
    client_name = (
        name if isinstance(name, str) and name.strip() else urlsplit(client_id).hostname
    )
    try:
        return MetadataDocumentClient(
            client_id=client_id,
            client_name=client_name,
            redirect_uris=[AnyUrl(uri) for uri in validated],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(_REQUESTABLE_SCOPES),
        )
    except ValidationError as exc:
        raise ClientMetadataError("client metadata document is invalid") from exc
