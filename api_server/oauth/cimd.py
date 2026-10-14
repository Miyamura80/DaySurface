"""OAuth Client ID Metadata Documents (CIMD) for DaySurface's OAuth server.

An MCP client such as claude.ai identifies itself with an HTTPS URL as its
``client_id``. That URL serves a JSON document describing the client (its name
and redirect URIs). This module fetches and validates that document, following
the MCP authorization spec (2026-07-28, Client Registration) and
draft-ietf-oauth-client-id-metadata-document-00.

The fetch targets a client-chosen URL, so it is treated as hostile:

* It goes through the shared SSRF guard: the host is resolved once, every
  address must be public, and the connection is pinned to a validated IP.
  Redirects are not followed.
* The whole fetch has a 5 second deadline (not just per read, which a server
  trickling bytes could stretch for hours) and a 10 KB cap on the bytes as sent,
  with compression refused so the cap cannot be inflated past.
* At most four fetches run at once; any beyond that fail immediately rather than
  queue. Fetches share the worker threads that serve /mcp, so a flood of slow
  client ids must not be able to occupy them.
* Results are cached: a valid document for five minutes, a failure for one.
"""

import json
import threading
import time
from urllib.parse import urlsplit

import httpx
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, ValidationError

from api_server.auth.scopes import ALL_SCOPES
from services._ssrf_guard import SsrfError, build_client, validate_and_pin

_FETCH_DEADLINE_SECONDS = 5.0
_MAX_DOCUMENT_BYTES = 10_000
_MAX_CLIENT_ID_LENGTH = 2000  # oauth_codes.client_id is String(2048)
_CACHE_SECONDS = 300
_FAILURE_CACHE_SECONDS = 60
_MAX_CONCURRENT_FETCHES = 4
# Scopes a client may request. The identity scopes are what MCP clients ask for
# when they want refresh tokens and a profile; the rest are DaySurface's own.
# A token always grants full access unless it carries DaySurface scopes (see
# api_server.auth.authkit_auth._resolve_scopes), so this list only gates what a
# request may name, not what it gets.
_REQUESTABLE_SCOPES = sorted(
    {"openid", "profile", "email", "offline_access", *ALL_SCOPES}
)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_cache: dict[str, tuple[float, "OAuthClientInformationFull"]] = {}
_failures: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()
_fetch_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_FETCHES)


class ClientMetadataError(ValueError):
    """The client_id is not a usable Client ID Metadata Document."""


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
    parts = urlsplit(uri)
    if parts.fragment:
        raise ClientMetadataError("redirect_uris must not contain a fragment")
    if parts.scheme == "https" and parts.hostname:
        return uri
    if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
        return uri
    raise ClientMetadataError("redirect_uris must use https or a loopback address")


def _fetch_document(client_id: str) -> bytes:
    try:
        target = validate_and_pin(client_id)
    except SsrfError as exc:
        raise ClientMetadataError(f"client_id host is not allowed: {exc}") from exc
    # Ask for the bytes as stored, so the size cap applies to what is sent.
    headers = {
        **target.headers,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    deadline = time.monotonic() + _FETCH_DEADLINE_SECONDS
    try:
        with (
            build_client(timeout=_FETCH_DEADLINE_SECONDS) as client,
            client.stream(
                "GET", target.url, headers=headers, extensions=target.extensions
            ) as response,
        ):
            if response.status_code != 200:
                raise ClientMetadataError(
                    f"client metadata fetch returned HTTP {response.status_code}"
                )
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise ClientMetadataError(
                    "client metadata document must not be compressed"
                )
            body = bytearray()
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > _MAX_DOCUMENT_BYTES:
                    raise ClientMetadataError("client metadata document is too large")
                if time.monotonic() > deadline:
                    raise ClientMetadataError("client metadata document took too long")
            return bytes(body)
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


def resolve_client(client_id: str) -> OAuthClientInformationFull:
    """Return the validated client for ``client_id``, fetching it if not cached.

    Raises :class:`ClientMetadataError` when the URL or its document is not
    acceptable, when it failed within the last minute, or when the fetch limit
    is reached.
    """
    if not is_metadata_document_client_id(client_id):
        raise ClientMetadataError("client_id is not a Client ID Metadata Document URL")
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(client_id)
        if cached and cached[0] > now:
            return cached[1]
        failed = _failures.get(client_id)
        if failed and failed[0] > now:
            raise ClientMetadataError(failed[1])
    if not _fetch_slots.acquire(blocking=False):
        raise ClientMetadataError("too many client metadata fetches in progress")
    try:
        client = parse_document(client_id, _fetch_document(client_id))
    except ClientMetadataError as exc:
        with _cache_lock:
            _failures[client_id] = (time.monotonic() + _FAILURE_CACHE_SECONDS, str(exc))
        raise
    finally:
        _fetch_slots.release()
    with _cache_lock:
        _cache[client_id] = (time.monotonic() + _CACHE_SECONDS, client)
        _failures.pop(client_id, None)
    return client


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _failures.clear()
