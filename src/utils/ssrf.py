"""Shared SSRF guard: resolve a URL's host and pin the connection to it.

Both the outbound image proxy (`services/image_proxy.py`) and webhook delivery
(`services/webhooks_svc.py`) must fetch/POST to user-supplied URLs without being
tricked into reaching cloud-metadata endpoints or internal services, including
via DNS rebinding between resolution and connection. This module is the one
implementation of that mechanic so a fix to the pinning/IPv6/SNI handling
protects every SSRF surface at once, rather than one copy silently drifting from
another.

Callers supply two things that legitimately differ per surface:

- ``validate_ip``: raises ``error_cls`` to reject a resolved address. The image
  proxy rejects anything not globally routable; webhook delivery allows loopback
  in dev but rejects private/reserved/metadata ranges.
- ``error_cls``: the exception type that surface already raises.

Scheme policy also differs (the image proxy allows cleartext http; webhooks
require https except to a dev loopback), so callers validate the scheme
*before* calling here; this function only re-checks that it is http(s).
"""

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


_Auth = tuple[str, str] | None


def _authority(hostpart: str, port: int | None) -> str:
    """Build a credential-free netloc: host/IP (bracketed for IPv6) + explicit port.

    Preserves an explicit port including ``:0`` so pinning never silently changes
    the destination port. Userinfo is intentionally excluded - see ``_extract_auth``.
    """
    netloc = f"[{hostpart}]" if ":" in hostpart else hostpart
    if port is not None:
        netloc += f":{port}"
    return netloc


def _extract_auth(parsed: Any) -> _Auth:
    """Return ``(user, password)`` basic-auth credentials from a URL, or ``None``.

    Credentials are carried out-of-band (for httpx's ``auth=``) rather than left
    in the URL, so pinning preserves authentication without embedding secrets in
    the request URL - which would otherwise leak into error messages and logs.
    """
    if parsed.username is None:
        return None
    return (parsed.username, parsed.password or "")


def pin_url_to_validated_ip(
    url: str,
    *,
    validate_ip: Callable[[_IpAddress], None],
    error_cls: type[Exception],
) -> tuple[str, dict[str, str], dict[str, str], _Auth]:
    """Resolve ``url``'s host, validate every answer, and pin to one address.

    Returns ``(pinned_url, headers, extensions, auth)``: the URL rewritten to
    target a validated IP with the original hostname preserved in the ``Host``
    header and (for https) TLS SNI, so the socket connects to the exact IP that
    was checked - closing the resolve-then-reconnect DNS-rebinding window. Any
    ``user:pass@`` credentials are returned as ``auth`` (for httpx's ``auth=``)
    and stripped from ``pinned_url`` so they never leak into error logs.
    IP-literal hosts are validated and passed through (with credentials stripped).
    Fails **closed**: an unresolvable host raises ``error_cls``.

    ``validate_ip`` is called for *every* resolved address and must raise
    ``error_cls`` to reject; the connection is pinned to the first address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise error_cls(f"unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise error_cls("URL has no host")
    try:
        port = parsed.port  # raises ValueError on e.g. "https://h:abc/"
    except ValueError as exc:
        raise error_cls(f"invalid port in URL {url!r}") from exc
    default_port = 80 if parsed.scheme == "http" else 443
    try:
        infos = socket.getaddrinfo(host, port or default_port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise error_cls(f"cannot resolve host {host!r}") from exc
    if not infos:
        raise error_cls(f"cannot resolve host {host!r}")

    addresses: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])  # strip scope id
        validate_ip(ip)
        addresses.append(str(ip))

    auth = _extract_auth(parsed)

    try:
        ipaddress.ip_address(host)
        # Host is an IP literal (validated above): nothing to pin, but still
        # strip any userinfo from the URL so credentials don't leak downstream.
        clean = parsed._replace(netloc=_authority(host, port)).geturl()
        return clean, {}, {}, auth
    except ValueError:
        pass

    ip_netloc = _authority(addresses[0], port)
    pinned = parsed._replace(netloc=ip_netloc).geturl()
    host_header = host if port is None else f"{host}:{port}"
    headers = {"Host": host_header}
    # httpcore uses sni_hostname for both the TLS handshake and certificate
    # hostname verification, so cert checks still run against the real host.
    extensions: dict[str, Any] = (
        {"sni_hostname": host} if parsed.scheme == "https" else {}
    )
    return pinned, headers, extensions, auth
