"""Shared SSRF guard: resolve a URL's host, reject non-global addresses, and
pin the connection to a validated IP.

Every outbound request to a user- or attacker-chosen URL goes through here:
remote email images (:mod:`services.image_proxy`), webhook delivery
(:mod:`services.webhook_delivery_svc`) and OAuth client metadata documents
(:mod:`api_server.oauth.cimd`). One hardened resolve-then-pin path and one
deadline-bound client, instead of each caller re-implementing them and
drifting.

The core defence is closing the DNS-rebinding TOCTOU window. A naive client that
validates ``example.com`` and then reconnects by hostname resolves DNS a second
time, so an attacker who flips the record to ``169.254.169.254`` (cloud
metadata), ``127.0.0.1``, or an RFC1918 range between the two lookups lands the
socket on internal infrastructure. Here the host is resolved exactly once; every
answer must pass the allowlist; and the request is rewritten to target one
validated IP with the original hostname carried in the ``Host`` header and TLS
SNI - so the IP that passed validation is the IP actually connected to.

Allowlist, not blocklist: an address passes only if
``ipaddress.ip_address(...).is_global`` is True. That is strictly safer than
enumerating bad ranges - one predicate rejects loopback, RFC1918 private,
CGNAT / shared address space (100.64/10), link-local (169.254/16, fe80::/10),
unique-local IPv6 (fc00::/7), multicast, and reserved ranges, so a range an
enumerated blocklist forgot cannot slip through. Handles IPv4 and IPv6.

A hostname resolving to MULTIPLE addresses is rejected if ANY answer is
non-global (defeats split-horizon DNS that returns a public A record to the
validator and a private one to the connector); the pin then targets one of the
validated answers.

:func:`validate_and_pin` decides *where* to connect and *what identity* to
present; :func:`build_client` returns the only client to send it with:
``trust_env=False`` (an environment proxy could route past this local DNS
check), ``follow_redirects=False`` (each hop must be re-validated and
re-pinned, never followed blindly) and a wall-clock deadline on every socket
operation.

Async callers (the CIMD resolver) use :func:`validate_and_pin_async` and
:func:`build_async_client`: the same checks, no worker thread held. DNS has no
async form and no timeout, so it runs on :class:`ResolverThreads`, which also
gives sync callers (webhook delivery) a timed lookup.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import ParseResult, urlparse

import anyio
import anyio.from_thread
import anyio.lowlevel
import httpcore
import httpx

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_ALLOWED_SCHEMES = ("http", "https")


class SsrfError(ValueError):
    """Raised when a URL is rejected by the SSRF guard.

    Covers an unsupported scheme, a missing/invalid host or port, a host that
    cannot be resolved, and - the security-critical case - a host that resolves
    to any non-global address.
    """


class UnresolvableHostError(SsrfError):
    """The host could not be resolved; unlike a refusal, this may be transient."""


@dataclass(frozen=True)
class PinnedTarget:
    """A validated request target with the connection pinned to a resolved IP.

    ``url`` points at the validated IP; ``headers`` carries the original ``Host``
    so virtual-hosted servers still route correctly; ``extensions`` sets
    ``sni_hostname`` for https so the TLS handshake and certificate hostname
    verification still run against the real hostname (httpcore uses
    ``sni_hostname`` for both). For an IP-literal host there is nothing to pin:
    ``url`` is returned unchanged and ``headers`` / ``extensions`` are empty.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)


def _addresses(host: str, infos: list) -> list[_IPAddress]:
    """The addresses in a ``getaddrinfo`` answer, or raise :class:`SsrfError`."""
    if not infos:
        raise UnresolvableHostError(f"cannot resolve host {host!r}")
    addresses: list[_IPAddress] = []
    for info in infos:
        addr = str(info[4][0]).split("%")[0]  # strip IPv6 scope id
        addresses.append(ipaddress.ip_address(addr))
    return addresses


def _resolve(host: str, port: int) -> list[_IPAddress]:
    """Resolve ``host`` to every candidate address, or raise :class:`SsrfError`."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:  # socket.gaierror is an OSError
        raise UnresolvableHostError(f"cannot resolve host {host!r}") from exc
    return _addresses(host, infos)


# IPv6 ranges that carry or translate to an IPv4 address and that ``is_global``
# reports as global on some Python versions: NAT64 (RFC 6052 / RFC 8215) and the
# deprecated IPv4-compatible block. On a NAT64 network, 64:ff9b::7f00:1 reaches
# 127.0.0.1, so these are refused outright.
_TRANSLATED_IPV6 = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("::/96"),
)


def is_public_address(addr: _IPAddress, *, allow_loopback: bool = False) -> bool:
    """True if ``addr`` may be connected to under the allowlist.

    Public means ``is_global``. ``allow_loopback`` additionally permits loopback
    (127.0.0.0/8, ::1) for local development, and nothing else. An IPv6 address
    that embeds an IPv4 address (IPv4-mapped, 6to4, Teredo) must also pass on
    that IPv4 address, and NAT64 / IPv4-compatible addresses are refused.
    """
    if allow_loopback and addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv6Address):
        if any(addr in net for net in _TRANSLATED_IPV6):
            return False
        embedded = [addr.ipv4_mapped, addr.sixtofour]
        if addr.teredo:
            embedded.extend(addr.teredo)
        if any(v4 is not None and not v4.is_global for v4 in embedded):
            return False
    return addr.is_global


@dataclass(frozen=True, slots=True)
class _Url:
    parsed: ParseResult
    host: str
    port: int | None  # as written in the URL
    connect_port: int  # the port to resolve and connect to


def _parse_url(url: str) -> _Url:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(f"unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SsrfError("URL has no host")
    try:
        port = parsed.port  # raises ValueError on e.g. "https://h:abc/"
    except ValueError as exc:
        raise SsrfError(f"invalid port in URL {url!r}") from exc
    default_port = 80 if parsed.scheme == "http" else 443
    return _Url(parsed, host, port, port or default_port)


def _pin(
    url: str, target: _Url, addresses: list[_IPAddress], *, allow_loopback: bool
) -> PinnedTarget:
    """Check every answer, then pin ``url`` to one. Shared by both paths."""
    host, port = target.host, target.port
    for addr in addresses:
        if not is_public_address(addr, allow_loopback=allow_loopback):
            # The address itself stays out of the message: it can name
            # internal infrastructure, and callers pass this text on.
            raise SsrfError(f"host {host!r} resolves to a non-public address")

    try:
        ipaddress.ip_address(host)
        # Host is already an IP literal (validated above): nothing to pin.
        return PinnedTarget(url=url)
    except ValueError:
        pass

    # Every answer passed; connect to the first IPv4 one when there is one,
    # since IPv6 egress is the likelier to be missing on a server.
    ip = str(next((a for a in addresses if a.version == 4), addresses[0]))
    ip_netloc = f"[{ip}]" if ":" in ip else ip
    if port:
        ip_netloc += f":{port}"
    pinned = target.parsed._replace(netloc=ip_netloc).geturl()
    host_header = host if not port else f"{host}:{port}"
    # sni_hostname keeps cert verification pointed at the real host under https.
    extensions = {"sni_hostname": host} if target.parsed.scheme == "https" else {}
    return PinnedTarget(
        url=pinned, headers={"Host": host_header}, extensions=extensions
    )


def validate_and_pin(
    url: str,
    *,
    allow_loopback: bool = False,
    resolver: ResolverThreads | None = None,
    timeout: float | None = None,
) -> PinnedTarget:
    """Validate ``url`` and pin it to one resolved, allowlisted IP.

    Resolves the host once, requires every answer to satisfy
    :func:`is_public_address`, then rewrites the URL to target one validated
    IP (the first IPv4 answer, else the first) while preserving the hostname in ``Host`` + SNI. IP-literal
    hosts are validated and passed through unchanged.

    With ``resolver`` and ``timeout``, DNS runs on one of ``resolver``'s threads
    and gives up after ``timeout`` seconds (raising
    :class:`UnresolvableHostError`), so a host whose DNS never answers cannot
    hold the caller; ``getaddrinfo`` itself has no timeout.

    Raises :class:`SsrfError` on any rejection, and :class:`ResolverBusyError`
    when ``resolver`` refuses the lookup.
    """
    target = _parse_url(url)
    if resolver is not None and timeout is not None:
        infos = resolver.getaddrinfo_blocking(target.host, target.connect_port, timeout)
        addresses = _addresses(target.host, infos)
    else:
        addresses = _resolve(target.host, target.connect_port)
    return _pin(url, target, addresses, allow_loopback=allow_loopback)


def build_client(*, timeout: float, deadline: float) -> httpx.Client:
    """An ``httpx.Client`` hardened for SSRF-guarded requests.

    ``trust_env=False`` so an environment proxy cannot route past the local DNS
    validation; ``follow_redirects=False`` so a 3xx to an internal address is
    never followed automatically (the caller re-validates + re-pins each hop, or
    treats the redirect as a failure).

    ``timeout`` bounds each socket operation, not the request: a server that
    sends one byte just inside it, or an endless run of ``100 Continue``
    responses, keeps a request alive indefinitely. ``deadline`` (a
    :func:`time.monotonic` value) bounds the whole exchange: every connect, TLS
    handshake, read and write is capped at the time left, and fails with a
    timeout once it has run out, wherever in the response the server stalls
    (status line, headers, body, chunk trailers). It is required because every
    URL fetched through this client is chosen by someone else.
    """
    return httpx.Client(
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        transport=_DeadlineTransport(deadline),
    )


class _DeadlineTransport(httpx.HTTPTransport):
    """httpx's own transport over a network backend that obeys ``deadline``.

    ``HTTPTransport`` accepts no network backend, so the pool it builds is
    replaced by one over :class:`_DeadlineBackend`. Everything else - request
    translation and the httpcore-to-httpx exception mapping - stays httpx's
    own, so a deadline hit surfaces as the usual ``httpx.ReadTimeout`` /
    ``ConnectTimeout`` / ``WriteTimeout``. ``_pool`` is private to httpx;
    ``tests/test_ssrf_guard.py`` pins that the swap still takes effect.
    """

    def __init__(self, deadline: float) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_DeadlineBackend(httpcore.SyncBackend(), deadline),
        )


def _time_left(deadline: float, timeout: float | None, exc: type[Exception]) -> float:
    """The timeout for one operation: ``timeout`` capped at the time left."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise exc("request deadline exceeded")
    return left if timeout is None else min(timeout, left)


class _DeadlineStream(httpcore.NetworkStream):
    """A network stream whose every operation is capped at a shared deadline."""

    def __init__(self, stream: httpcore.NetworkStream, deadline: float) -> None:
        self._stream = stream
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        timeout = _time_left(self._deadline, timeout, httpcore.ReadTimeout)
        return self._stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        timeout = _time_left(self._deadline, timeout, httpcore.WriteTimeout)
        self._stream.write(buffer, timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        timeout = _time_left(self._deadline, timeout, httpcore.ConnectTimeout)
        stream = self._stream.start_tls(ssl_context, server_hostname, timeout)
        return _DeadlineStream(stream, self._deadline)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


# httpcore's socket-option type (``httpcore._backends.base.SOCKET_OPTION``),
# restated so the overrides below need no private import.
_SocketOption = (
    tuple[int, int, int]
    | tuple[int, int, bytes | bytearray]
    | tuple[int, int, None, int]
)


class _DeadlineBackend(httpcore.NetworkBackend):
    """Wraps an httpcore backend so every connection it opens obeys ``deadline``."""

    def __init__(self, backend: httpcore.NetworkBackend, deadline: float) -> None:
        self._backend = backend
        self._deadline = deadline

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        timeout = _time_left(self._deadline, timeout, httpcore.ConnectTimeout)
        stream = self._backend.connect_tcp(
            host, port, timeout, local_address, socket_options
        )
        return _DeadlineStream(stream, self._deadline)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


# -- Resolver threads and the async path -------------------------------------


class ResolverBusyError(RuntimeError):
    """Every resolver thread is still inside a lookup."""


class HostLookupRunningError(ResolverBusyError):
    """A lookup of this same host is still running: one hung host, one thread."""


@dataclass(slots=True)
class _Lookup:
    """The result of one ``getaddrinfo`` run on its own thread."""

    answer: list = field(default_factory=list)
    failure: OSError | None = None


class ResolverThreads:
    """Runs ``getaddrinfo`` on at most ``limit`` threads of its own.

    A thread cannot be interrupted, so a caller whose deadline passes stops
    waiting but leaves the lookup running until the system resolver gives up.
    Each thread is therefore counted from start until ``getaddrinfo`` really
    returns, not until its caller stops waiting. A lookup is refused at once
    when every thread is taken (:class:`ResolverBusyError`) or when a lookup
    of the same host is still running (:class:`HostLookupRunningError`), so
    one slow host holds at most one thread however often it is asked for.
    They are this class's own threads, so a hung lookup never holds one of
    anyio's worker threads (the pool that serves /mcp and sync routes).
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._running = 0
        self._hosts: set[str] = set()  # hosts with a lookup still running

    @property
    def running(self) -> int:
        with self._lock:
            return self._running

    def _start(self, host: str, port: int, on_done: Callable[[], None]) -> _Lookup:
        key = host.lower()
        with self._lock:
            if key in self._hosts:
                raise HostLookupRunningError
            if self._running >= self._limit:
                raise ResolverBusyError
            self._running += 1
            self._hosts.add(key)
        lookup = _Lookup()

        def run() -> None:
            try:
                lookup.answer.extend(
                    socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
                )
            except OSError as exc:  # socket.gaierror is an OSError
                lookup.failure = exc
            finally:
                with self._lock:
                    self._running -= 1
                    self._hosts.discard(key)
                on_done()

        try:
            threading.Thread(target=run, name="ssrf-dns", daemon=True).start()
        except RuntimeError:  # no thread could be started
            with self._lock:
                self._running -= 1
                self._hosts.discard(key)
            raise
        return lookup

    @staticmethod
    def _answer(host: str, lookup: _Lookup) -> list:
        if lookup.failure is None:
            return lookup.answer
        raise UnresolvableHostError(f"cannot resolve host {host!r}") from lookup.failure

    async def getaddrinfo(self, host: str, port: int) -> list:
        """Resolve for an async caller; cancel the caller to stop waiting."""
        done = anyio.Event()
        token = anyio.lowlevel.current_token()

        def wake() -> None:
            # RuntimeError: the event loop is gone, so nobody waits.
            with suppress(RuntimeError):
                anyio.from_thread.run_sync(done.set, token=token)

        lookup = self._start(host, port, wake)
        await done.wait()
        return self._answer(host, lookup)

    def getaddrinfo_blocking(self, host: str, port: int, timeout: float) -> list:
        """Resolve for a sync caller, giving up after ``timeout`` seconds."""
        done = threading.Event()
        lookup = self._start(host, port, done.set)
        if not done.wait(max(timeout, 0.0)):
            raise UnresolvableHostError(f"timed out resolving host {host!r}")
        return self._answer(host, lookup)


async def validate_and_pin_async(
    url: str, *, resolver: ResolverThreads, allow_loopback: bool = False
) -> PinnedTarget:
    """:func:`validate_and_pin` for async callers; DNS runs on ``resolver``.

    Cancel the caller (for example with :func:`anyio.fail_after`) to bound the
    lookup: the wait ends at once and the thread is released when the system
    resolver returns. Raises :class:`ResolverBusyError` (or its subclass
    :class:`HostLookupRunningError`) when ``resolver`` refuses the lookup, and
    :class:`SsrfError` on any rejection.
    """
    target = _parse_url(url)
    try:
        ipaddress.ip_address(target.host)
    except ValueError:
        infos = await resolver.getaddrinfo(target.host, target.connect_port)
    else:  # an IP literal: no lookup, and no thread
        infos = [(None, None, None, "", (target.host, target.connect_port))]
    addresses = _addresses(target.host, infos)
    return _pin(url, target, addresses, allow_loopback=allow_loopback)


def build_async_client(*, timeout: float) -> httpx.AsyncClient:
    """The ``httpx.AsyncClient`` counterpart of :func:`build_client`.

    The same hardening: no environment proxy, no redirects. There is no
    deadline transport: an async caller bounds the whole exchange by
    cancelling it (``anyio.fail_after``), which interrupts any await,
    wherever in the response the server stalls. ``timeout`` still bounds each
    socket operation.
    """
    return httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)
