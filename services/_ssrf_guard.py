"""Shared SSRF guard: resolve a URL's host, reject non-global addresses, and
pin the connection to a validated IP.

Factored out of :mod:`services.image_proxy` so every outbound request that
targets a user- or attacker-controlled URL (remote email images, webhook
delivery) shares one hardened resolve-then-pin path instead of each transport
re-implementing it and drifting.

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

The caller still owns the client: this module only decides *where* to connect
and *what identity* to present. Callers MUST construct their ``httpx`` client
with ``trust_env=False`` (an environment proxy could route past this local DNS
check) and ``follow_redirects=False`` (each hop must be re-validated and
re-pinned, never followed blindly) - see :func:`build_client`.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_ALLOWED_SCHEMES = ("http", "https")


class SsrfError(ValueError):
    """Raised when a URL is rejected by the SSRF guard.

    Covers an unsupported scheme, a missing/invalid host or port, a host that
    cannot be resolved, and - the security-critical case - a host that resolves
    to any non-global address.
    """


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


def _resolve(host: str, port: int) -> list[_IPAddress]:
    """Resolve ``host`` to every candidate address, or raise :class:`SsrfError`."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        raise SsrfError(f"cannot resolve host {host!r}") from exc
    if not infos:
        raise SsrfError(f"cannot resolve host {host!r}")
    addresses: list[_IPAddress] = []
    for info in infos:
        addr = str(info[4][0]).split("%")[0]  # strip IPv6 scope id
        addresses.append(ipaddress.ip_address(addr))
    return addresses


def is_public_address(addr: _IPAddress, *, allow_loopback: bool = False) -> bool:
    """True if ``addr`` may be connected to under the allowlist.

    Public means ``is_global``. ``allow_loopback`` additionally permits loopback
    (127.0.0.0/8, ::1) for local development, and nothing else.
    """
    if allow_loopback and addr.is_loopback:
        return True
    return addr.is_global


def validate_and_pin(url: str, *, allow_loopback: bool = False) -> PinnedTarget:
    """Validate ``url`` and pin it to one resolved, allowlisted IP.

    Resolves the host once, requires every answer to satisfy
    :func:`is_public_address`, then rewrites the URL to target the first
    validated IP while preserving the hostname in ``Host`` + SNI. IP-literal
    hosts are validated and passed through unchanged.

    Raises :class:`SsrfError` on any rejection.
    """
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
    addresses = _resolve(host, port or default_port)
    for addr in addresses:
        if not is_public_address(addr, allow_loopback=allow_loopback):
            raise SsrfError(f"host {host!r} resolves to non-public address {addr}")

    try:
        ipaddress.ip_address(host)
        # Host is already an IP literal (validated above): nothing to pin.
        return PinnedTarget(url=url)
    except ValueError:
        pass

    ip = str(addresses[0])
    ip_netloc = f"[{ip}]" if ":" in ip else ip
    if port:
        ip_netloc += f":{port}"
    pinned = parsed._replace(netloc=ip_netloc).geturl()
    host_header = host if not port else f"{host}:{port}"
    # sni_hostname keeps cert verification pointed at the real host under https.
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return PinnedTarget(
        url=pinned, headers={"Host": host_header}, extensions=extensions
    )


def build_client(*, timeout: float) -> httpx.Client:
    """An ``httpx.Client`` hardened for SSRF-guarded requests.

    ``trust_env=False`` so an environment proxy cannot route past the local DNS
    validation; ``follow_redirects=False`` so a 3xx to an internal address is
    never followed automatically (the caller re-validates + re-pins each hop, or
    treats the redirect as a failure).
    """
    return httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)
