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
* Each resolve has one deadline, ``_FETCH_DEADLINE_SECONDS``, started before
  anything else. It is enforced on every connect, TLS handshake, read and write
  (see :func:`services._ssrf_guard.build_client`) and between body chunks, so
  a server that trickles its headers, floods ``100 Continue`` responses or
  stalls in a chunk trailer is cut off at the deadline, not merely at each
  per-read timeout. The body is capped at ``_MAX_DOCUMENT_BYTES`` as sent, with
  compression refused so the cap cannot be inflated past.
* DNS is the one step the deadline cannot interrupt: ``getaddrinfo`` has no
  timeout, so a slow answer is bounded only by the system resolver's own
  timeout and retries. It runs before a fetch slot is taken (the slot is keyed
  on its answer), so it is not counted against the fetch caps either. What
  bounds it is that the time it takes is charged to the deadline (a slow
  answer leaves the fetch nothing) and that concurrent resolves of one
  ``client_id`` share a single lookup.
* Fetches are capped by :class:`services._ssrf_guard.FetchLimiter`:
  ``_MAX_CONCURRENT_FETCHES`` in total and ``_MAX_FETCHES_PER_TARGET`` per
  pinned network, refused at once when full. Fetches share the worker threads
  that serve /mcp, so a flood of slow client ids must not occupy them. The cap
  is keyed on the pinned address, not the hostname, because subdomains are
  free to mint.
* Concurrent resolves of one ``client_id`` share one fetch (single flight):
  the first does the work and the rest wait for its result, without a slot.
* Results are cached: a valid document is fresh for ``_CACHE_SECONDS`` and a
  failure for ``_FAILURE_CACHE_SECONDS``. A valid document is then kept for up
  to ``_MAX_STALE_SECONDS`` more, and served stale when its refetch finds the
  caps full, so filling the caps with slow targets locks out only clients this
  server has never seen. A full cap is not cached as a failure, so it cannot
  poison a legitimate client id. Each cache holds at most
  ``_MAX_CACHE_ENTRIES``, evicting expired entries first and then the least
  recently used.
"""

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, ValidationError

from api_server.auth.scopes import ALL_SCOPES
from services._ssrf_guard import (
    FetchLimitBusyError,
    FetchLimiter,
    FetchLimitSnapshot,
    PinnedTarget,
    SsrfError,
    build_client,
    validate_and_pin,
)

_FETCH_DEADLINE_SECONDS = 5.0
_MAX_DOCUMENT_BYTES = 10_000
_MAX_CLIENT_ID_LENGTH = 2000  # oauth_codes.client_id is String(2048)
_CACHE_SECONDS = 300
_FAILURE_CACHE_SECONDS = 60
_MAX_STALE_SECONDS = 24 * 3600
# Fetch caps. Well below the default 40 worker threads the fetches share with
# /mcp, and wide enough per target for a burst of sign-ins to one client.
_MAX_CONCURRENT_FETCHES = 8
_MAX_FETCHES_PER_TARGET = 2
_MAX_CACHE_ENTRIES = 1000
_BUSY_MESSAGE = "too many client metadata fetches in progress"
# Scopes a client may request. The identity scopes are what MCP clients ask for
# when they want refresh tokens and a profile; the rest are DaySurface's own.
# A token always grants full access unless it carries DaySurface scopes (see
# api_server.auth.authkit_auth._resolve_scopes), so this list only gates what a
# request may name, not what it gets.
_REQUESTABLE_SCOPES = sorted(
    {"openid", "profile", "email", "offline_access", *ALL_SCOPES}
)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True, slots=True)
class _Entry[T]:
    fresh_until: float
    keep_until: float  # served stale until then; equals fresh_until for failures
    value: T


@dataclass(slots=True)
class _Flight:
    """One fetch in progress, which later resolves of its client_id wait on."""

    done: threading.Event = field(default_factory=threading.Event)
    client: OAuthClientInformationFull | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CimdSnapshot:
    """The resolver's state at one moment, for tests and diagnostics."""

    cached: tuple[str, ...]
    failed: tuple[str, ...]
    in_flight: tuple[str, ...]
    fetches: FetchLimitSnapshot


# All three guarded by _lock. Insertion order is recency: oldest first.
_cache: OrderedDict[str, _Entry[OAuthClientInformationFull]] = OrderedDict()
_failures: OrderedDict[str, _Entry[str]] = OrderedDict()
_in_flight: dict[str, _Flight] = {}
_lock = threading.Lock()
_fetch_limiter = FetchLimiter(
    total=_MAX_CONCURRENT_FETCHES, per_target=_MAX_FETCHES_PER_TARGET
)


def _remember[T](
    cache: OrderedDict[str, _Entry[T]], key: str, entry: _Entry[T]
) -> None:
    """Store ``entry``, keeping ``cache`` bounded. Hold _lock."""
    cache.pop(key, None)
    if len(cache) >= _MAX_CACHE_ENTRIES:
        now = time.monotonic()
        for old in [old for old, kept in cache.items() if kept.keep_until <= now]:
            del cache[old]
    while len(cache) >= _MAX_CACHE_ENTRIES:
        cache.popitem(last=False)
    cache[key] = entry


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


def _fetch_document(target: PinnedTarget, deadline: float) -> bytes:
    # Ask for the bytes as stored, so the size cap applies to what is sent.
    headers = {
        **target.headers,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    try:
        with (
            build_client(timeout=_FETCH_DEADLINE_SECONDS, deadline=deadline) as client,
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
                if time.monotonic() > deadline:
                    raise ClientMetadataError("client metadata document took too long")
                if len(body) > _MAX_DOCUMENT_BYTES:
                    raise ClientMetadataError("client metadata document is too large")
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


def _lookup(client_id: str) -> OAuthClientInformationFull | None:
    """The fresh cached client, or None. Raises a live cached failure. Hold _lock."""
    now = time.monotonic()
    cached = _cache.get(client_id)
    if cached and cached.fresh_until > now:
        _cache.move_to_end(client_id)
        return cached.value
    failed = _failures.get(client_id)
    if failed and failed.fresh_until > now:
        raise ClientMetadataError(failed.value)
    return None


def _stale(client_id: str) -> OAuthClientInformationFull | None:
    """The cached client if it is still within its staleness limit."""
    with _lock:
        cached = _cache.get(client_id)
        if cached and cached.keep_until > time.monotonic():
            _cache.move_to_end(client_id)
            return cached.value
    return None


def resolve_client(client_id: str) -> OAuthClientInformationFull:
    """Return the validated client for ``client_id``, fetching it if not cached.

    Raises :class:`ClientMetadataError` when the URL or its document is not
    acceptable, when it failed within ``_FAILURE_CACHE_SECONDS``, or when the
    fetch caps are full and there is no stale record to serve.
    """
    if not is_metadata_document_client_id(client_id):
        raise ClientMetadataError("client_id is not a Client ID Metadata Document URL")
    # Started first, so DNS and any wait for another fetch count against it.
    deadline = time.monotonic() + _FETCH_DEADLINE_SECONDS
    with _lock:
        if (client := _lookup(client_id)) is not None:
            return client
        flight = _in_flight.get(client_id)
        if flight is None:
            flight = _in_flight[client_id] = _Flight()
            leading = True
        else:
            leading = False
    if not leading:
        return _wait_for(flight, deadline)
    try:
        client = flight.client = _refresh(client_id, deadline)
    except ClientMetadataError as exc:
        flight.error = str(exc)
        raise
    finally:
        # The cache is written before the flight goes, so a resolve arriving
        # in between finds one or the other. Anything else that escaped (a
        # bug, or KeyboardInterrupt) leaves neither field set.
        with _lock:
            del _in_flight[client_id]
        flight.done.set()
    return client


def _wait_for(flight: _Flight, deadline: float) -> OAuthClientInformationFull:
    if not flight.done.wait(max(0.0, deadline - time.monotonic())):
        raise ClientMetadataError("client metadata document took too long")
    if flight.client is not None:
        return flight.client
    raise ClientMetadataError(flight.error or "client metadata fetch failed")


def _refresh(client_id: str, deadline: float) -> OAuthClientInformationFull:
    """Fetch and cache the client, or serve it stale if the caps are full."""
    try:
        client = _fetch(client_id, deadline)
    except FetchLimitBusyError:
        # Not cached as a failure: full caps say nothing about this client.
        if (stale := _stale(client_id)) is not None:
            return stale
        raise ClientMetadataError(_BUSY_MESSAGE) from None
    except ClientMetadataError as exc:
        until = time.monotonic() + _FAILURE_CACHE_SECONDS
        with _lock:
            _remember(_failures, client_id, _Entry(until, until, str(exc)))
        raise
    now = time.monotonic()
    entry = _Entry(
        now + _CACHE_SECONDS, now + _CACHE_SECONDS + _MAX_STALE_SECONDS, client
    )
    with _lock:
        _remember(_cache, client_id, entry)
        _failures.pop(client_id, None)
    return client


def _fetch(client_id: str, deadline: float) -> OAuthClientInformationFull:
    try:
        target = validate_and_pin(client_id)
    except SsrfError as exc:
        raise ClientMetadataError(f"client_id host is not allowed: {exc}") from exc
    if time.monotonic() >= deadline:
        raise ClientMetadataError("client metadata document took too long")
    with _fetch_limiter.hold(target.address):
        raw = _fetch_document(target, deadline)
    return parse_document(client_id, raw)


def snapshot() -> CimdSnapshot:
    with _lock:
        return CimdSnapshot(
            cached=tuple(_cache),
            failed=tuple(_failures),
            in_flight=tuple(_in_flight),
            fetches=_fetch_limiter.snapshot(),
        )


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _failures.clear()
