"""SSRF-guarded fetcher that turns remote email images into base64 payloads.

Why this exists: strict-CSP MCP hosts (claude.ai enforces
``img-src 'self' data: blob: https://assets.claude.ai`` on the app iframe)
block every image an email references from an arbitrary sender domain, while
lax hosts (Goose) load them straight from the network. The inbox reader app
therefore calls ``gmail_inbox.fetch_image`` and renders the bytes as a
``data:`` URI, which every conforming host allows.

The URL comes out of attacker-controlled email HTML, so this is a classic
SSRF surface. Guards, in order:

- scheme must be http/https; malformed URLs (bad port etc.) are rejected as
  :class:`ImageFetchError`, never surfaced as raw parse errors;
- every DNS answer for the host must be a *global* address (rejects
  loopback, RFC1918, link-local, CGNAT, multicast, reserved);
- the connection is PINNED to the validated address: the request targets the
  resolved IP with the original hostname preserved via the Host header and
  TLS SNI, so a DNS-rebinding flip between validation and connect cannot
  redirect the socket to a private address;
- ``trust_env=False``: proxy/env routing must not carry the request past the
  guard (an environment proxy could reach destinations this check can't see);
- redirects are followed manually and every hop re-validated + re-pinned;
- the response must declare an ``image/*`` content type;
- the body is streamed, capped at ``_MAX_IMAGE_BYTES``, and subject to a
  wall-clock deadline enforced between chunks (a trickling sender cannot
  hold a slot past ``_TOTAL_DEADLINE_SECONDS``).

Not an ``@service``: this is an app-only capability of the inbox reader, so
it must not be auto-registered as an LLM-facing MCP tool / CLI command / API
route the way registry services are.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from models.gmail import GmailFetchImageResult

_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # cap per image; emails shouldn't need more
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 10.0  # per network operation (httpx timeout)
# Wall-clock ceiling across all hops AND mid-stream: the frontend fires up to
# 20 of these in parallel per click, so neither a redirect chain nor a
# trickling body may hold a slot longer than this.
_TOTAL_DEADLINE_SECONDS = 20.0


class ImageFetchError(ValueError):
    """Raised when a remote image URL is rejected or cannot be fetched."""


def _validate_and_pin(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Validate ``url`` and return (pinned_url, headers, extensions).

    Resolves the host, requires every DNS answer to be a global address, then
    rewrites the URL to target one validated IP while preserving the original
    hostname in the Host header and (for https) TLS SNI - closing the
    resolve-then-reconnect DNS-rebinding window. IP-literal hosts are
    validated and passed through unchanged.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageFetchError(f"unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ImageFetchError("URL has no host")
    try:
        port = parsed.port  # raises ValueError on e.g. "https://h:abc/"
    except ValueError as exc:
        raise ImageFetchError(f"invalid port in URL {url!r}") from exc
    default_port = 80 if parsed.scheme == "http" else 443
    try:
        infos = socket.getaddrinfo(host, port or default_port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        raise ImageFetchError(f"cannot resolve host {host!r}") from exc
    if not infos:
        raise ImageFetchError(f"cannot resolve host {host!r}")
    addresses: list[str] = []
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise ImageFetchError(f"host {host!r} resolves to non-public address")
        addresses.append(str(addr))

    try:
        ipaddress.ip_address(host)
        # Host is already an IP literal (validated above): nothing to pin.
        return url, {}, {}
    except ValueError:
        pass

    ip = addresses[0]
    ip_netloc = f"[{ip}]" if ":" in ip else ip
    if port:
        ip_netloc += f":{port}"
    pinned = parsed._replace(netloc=ip_netloc).geturl()
    host_header = host if not port else f"{host}:{port}"
    headers = {"Host": host_header}
    # httpcore uses sni_hostname for both the TLS handshake and certificate
    # hostname verification, so cert checks still run against the real host.
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return pinned, headers, extensions


def _read_image_response(resp: Any, url: str, deadline: float) -> GmailFetchImageResult:
    """Validate a non-redirect response and stream its body within limits."""
    if resp.status_code != 200:
        raise ImageFetchError(f"image fetch returned HTTP {resp.status_code}")
    mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        raise ImageFetchError(f"not an image content type: {mime!r}")
    declared = resp.headers.get("content-length")
    if declared:
        try:
            declared_len = int(declared)
        except ValueError as exc:
            # ImageFetchError subclasses ValueError, so the size check must
            # stay OUTSIDE this try block.
            raise ImageFetchError(f"invalid Content-Length: {declared!r}") from exc
        if declared_len > _MAX_IMAGE_BYTES:
            raise ImageFetchError("image exceeds size limit")
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_bytes():
        if time.monotonic() > deadline:
            raise ImageFetchError("image fetch deadline exceeded")
        total += len(chunk)
        if total > _MAX_IMAGE_BYTES:
            raise ImageFetchError("image exceeds size limit")
        chunks.append(chunk)
    return GmailFetchImageResult(
        url=url,
        mime_type=mime,
        data_base64=base64.b64encode(b"".join(chunks)).decode("ascii"),
    )


def fetch_remote_image(url: str) -> GmailFetchImageResult:
    """Fetch one remote image with SSRF guards; return base64 bytes.

    Raises :class:`ImageFetchError` on any rejection (malformed URL, private
    address, non-image response, oversize body, deadline, network failure).
    """
    current = url
    deadline = time.monotonic() + _TOTAL_DEADLINE_SECONDS
    with httpx.Client(
        timeout=_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise ImageFetchError("image fetch deadline exceeded")
            target, headers, extensions = _validate_and_pin(current)
            try:
                with client.stream(
                    "GET", target, headers=headers, extensions=extensions
                ) as resp:
                    if resp.is_redirect:
                        # is_redirect implies a Location header exists. Join
                        # against the hostname form, not the pinned IP form.
                        location = resp.headers["location"]
                        current = str(httpx.URL(current).join(location))
                        continue
                    return _read_image_response(resp, url, deadline)
            except httpx.HTTPError as exc:
                raise ImageFetchError(f"image fetch failed: {exc}") from exc
    raise ImageFetchError("too many redirects")
