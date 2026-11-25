"""SSRF / DNS-rebinding tests for the shared guard and webhook delivery.

Two layers are exercised:

* ``services._ssrf_guard.validate_and_pin`` - the resolve-then-pin primitive:
  scheme allowlist, is_global-only DNS answers (v4 + v6), reject-if-ANY-answer
  is non-global, IP-literal passthrough, and the pinned-URL / Host / SNI shape.
* ``webhook_delivery_svc`` end to end - a subscription validated against a
  PUBLIC address at subscribe time whose host is flipped to an internal address
  before delivery (classic rebinding TOCTOU) must NOT connect; a legitimate
  public endpoint still delivers; a 3xx toward metadata is never followed.

All DNS and HTTP are mocked. Subscribe-time resolution is patched on
``services.webhooks_svc.socket`` and delivery-time resolution on
``services._ssrf_guard.socket``, so the two phases can be made to disagree -
that disagreement IS the rebind.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch

import httpcore
import httpx
import pytest

import services._ssrf_guard as ssrf_guard
from common import global_config
from db import engine as db_engine
from db.models.webhooks import WebhookDelivery
from models.webhooks import WebhookSubscribeInput
from services._ssrf_guard import (
    HostLookupRunningError,
    PinnedTarget,
    ResolverBusyError,
    ResolverThreads,
    SsrfError,
    UnresolvableHostError,
    build_client,
    validate_and_pin,
    validate_and_pin_async,
)
from services.webhook_delivery_svc import drain_due_deliveries
from services.webhooks_svc import enqueue_event, webhook_subscribe
from tests.test_template import TestTemplate
from tests.test_webhooks import _patch_db, _plaintext_encryption

_PUBLIC_IP = "93.184.216.34"
_PUBLIC_V6 = "2606:4700:4700::1111"


def _addrinfo(ip: str, *, port: int = 443):
    """A single-answer getaddrinfo result for ``ip`` (v4 or v6 by shape)."""
    if ":" in ip:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


_PUBLIC_ADDRINFO = _addrinfo(_PUBLIC_IP)

# Internal / non-global targets an attacker would rebind to, v4 and v6.
_INTERNAL_ADDRS = [
    "127.0.0.1",  # loopback
    "169.254.169.254",  # link-local cloud metadata
    "10.0.0.5",  # RFC1918 private
    "100.64.0.1",  # CGNAT / shared address space
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 unique-local
]


class _Recorder:
    """MockTransport handler that records every request it is asked to serve."""

    def __init__(self, response: httpx.Response | None = None):
        self.requests: list[httpx.Request] = []
        self._response = response or httpx.Response(200)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


# ---------------------------------------------------------------------------
# Unit: the shared resolve-then-pin primitive
# ---------------------------------------------------------------------------


class TestSsrfGuard(TestTemplate):
    def _pin(self, url: str, addrinfo, *, allow_loopback: bool = False) -> PinnedTarget:
        with patch.object(ssrf_guard.socket, "getaddrinfo", return_value=addrinfo):
            return validate_and_pin(url, allow_loopback=allow_loopback)

    def test_rejects_non_http_scheme(self):
        for url in ("ftp://example.com/x", "file:///etc/passwd", "gopher://h/"):
            with pytest.raises(SsrfError, match="scheme"):
                validate_and_pin(url)

    def test_rejects_missing_host_and_bad_port(self):
        with pytest.raises(SsrfError, match="no host"):
            validate_and_pin("https:///x")
        with pytest.raises(SsrfError, match="invalid port"):
            validate_and_pin("https://h:notaport/x")

    def test_pins_v4_to_validated_ip_with_host_and_sni(self):
        t = self._pin("https://hook.example/path", _PUBLIC_ADDRINFO)
        assert t.url == f"https://{_PUBLIC_IP}/path"
        assert t.headers["Host"] == "hook.example"
        assert t.extensions["sni_hostname"] == "hook.example"

    def test_pins_v6_in_brackets(self):
        t = self._pin("https://hook.example/path", _addrinfo(_PUBLIC_V6))
        assert t.url == f"https://[{_PUBLIC_V6}]/path"
        assert t.headers["Host"] == "hook.example"

    def test_preserves_explicit_port(self):
        t = self._pin("https://hook.example:8443/p", _addrinfo(_PUBLIC_IP, port=8443))
        assert t.url == f"https://{_PUBLIC_IP}:8443/p"
        assert t.headers["Host"] == "hook.example:8443"

    def test_http_scheme_has_no_sni_extension(self):
        t = self._pin("http://hook.example/p", _addrinfo(_PUBLIC_IP, port=80))
        assert t.extensions == {}

    def test_ip_literal_passes_through_unpinned(self):
        # A public IP literal is validated but there is nothing to pin.
        t = validate_and_pin(f"https://{_PUBLIC_IP}/p")
        assert t == PinnedTarget(url=f"https://{_PUBLIC_IP}/p")

    def test_rejects_non_global_single_answer(self):
        for ip in _INTERNAL_ADDRS:
            with pytest.raises(SsrfError, match="non-public"):
                self._pin("https://hook.example/p", _addrinfo(ip))

    def test_rejects_if_any_answer_non_global(self):
        # Split-horizon: a public answer alongside a private one must be
        # rejected wholesale, not cherry-picked down to the public one.
        mixed = _PUBLIC_ADDRINFO + _addrinfo("10.0.0.9")
        with pytest.raises(SsrfError, match="non-public"):
            self._pin("https://hook.example/p", mixed)

    def test_allow_loopback_permits_only_loopback(self):
        # Dev override: loopback is allowed, but a private/link-local host is
        # still rejected even with allow_loopback=True.
        t = self._pin(
            "http://localhost/p", _addrinfo("127.0.0.1", port=80), allow_loopback=True
        )
        assert t.url == "http://127.0.0.1/p"
        with pytest.raises(SsrfError, match="non-public"):
            self._pin(
                "https://hook.example/p",
                _addrinfo("169.254.169.254"),
                allow_loopback=True,
            )

    def test_unresolvable_host_raises(self):
        with (
            patch.object(ssrf_guard.socket, "getaddrinfo", side_effect=socket.gaierror),
            pytest.raises(SsrfError, match="cannot resolve"),
        ):
            validate_and_pin("https://nope.invalid/p")

    def test_deadline_client_keeps_the_hardening(self):
        client = build_client(timeout=1.0, deadline=time.monotonic() + 1.0)
        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
        finally:
            client.close()

    def test_deadline_transport_replaces_httpx_pool(self):
        # _DeadlineTransport swaps httpx's private HTTPTransport._pool; if an
        # httpx upgrade renames it, the deadline would silently stop applying.
        client = build_client(timeout=1.0, deadline=time.monotonic() + 1.0)
        try:
            transport = client._transport
            assert isinstance(transport, httpx.HTTPTransport)
            pool = transport._pool
            assert isinstance(pool, httpcore.ConnectionPool)
            assert isinstance(pool._network_backend, ssrf_guard._DeadlineBackend)
        finally:
            client.close()

    def test_deadline_client_fails_once_the_deadline_has_passed(self):
        client = build_client(timeout=1.0, deadline=time.monotonic() - 1.0)
        try:
            with pytest.raises(httpx.ConnectTimeout):
                client.get("http://127.0.0.1:9/")
        finally:
            client.close()


class TestAsyncPin(TestTemplate):
    """validate_and_pin_async: the same validation, DNS on capped threads."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    async def _pin(self, url: str, addrinfo, resolver=None) -> PinnedTarget:
        with patch.object(ssrf_guard.socket, "getaddrinfo", return_value=addrinfo):
            return await validate_and_pin_async(
                url, resolver=resolver or ResolverThreads(1)
            )

    @pytest.mark.anyio
    async def test_pins_like_the_sync_path(self):
        t = await self._pin("https://hook.example/path", _PUBLIC_ADDRINFO)
        assert t.url == f"https://{_PUBLIC_IP}/path"
        assert t.headers["Host"] == "hook.example"
        assert t.extensions["sni_hostname"] == "hook.example"

    @pytest.mark.anyio
    async def test_rejects_every_internal_answer(self):
        for ip in _INTERNAL_ADDRS:
            with pytest.raises(SsrfError, match="non-public") as info:
                await self._pin("https://hook.example/p", _addrinfo(ip))
            assert ip not in str(info.value)  # no internal address in the text
        mixed = _PUBLIC_ADDRINFO + _addrinfo("10.0.0.9")
        with pytest.raises(SsrfError, match="non-public"):
            await self._pin("https://hook.example/p", mixed)

    @pytest.mark.anyio
    async def test_rejects_scheme_and_internal_literals_without_a_lookup(self):
        busy = ResolverThreads(0)  # any lookup would be refused as busy
        with pytest.raises(SsrfError, match="scheme"):
            await validate_and_pin_async("file:///etc/passwd", resolver=busy)
        with pytest.raises(SsrfError, match="non-public"):
            await validate_and_pin_async("https://169.254.169.254/x", resolver=busy)
        t = await validate_and_pin_async(f"https://{_PUBLIC_IP}/p", resolver=busy)
        assert t == PinnedTarget(url=f"https://{_PUBLIC_IP}/p")

    @pytest.mark.anyio
    async def test_unresolvable_host_is_its_own_error(self):
        resolver = ResolverThreads(1)
        with (
            patch.object(ssrf_guard.socket, "getaddrinfo", side_effect=socket.gaierror),
            pytest.raises(UnresolvableHostError, match="cannot resolve"),
        ):
            await validate_and_pin_async("https://nope.invalid/p", resolver=resolver)
        assert resolver.running == 0

    @pytest.mark.anyio
    async def test_busy_resolver_refuses_at_once(self):
        with pytest.raises(ResolverBusyError):
            await self._pin(
                "https://hook.example/p", _PUBLIC_ADDRINFO, ResolverThreads(0)
            )


class TestTimedResolve(TestTemplate):
    """Sync callers (webhook delivery) bound DNS with a timed lookup."""

    def test_a_hanging_lookup_times_out_and_holds_one_thread(self):
        release = threading.Event()

        def hang(*_args, **_kwargs):
            release.wait(10)
            return _PUBLIC_ADDRINFO

        threads = ResolverThreads(4)
        with patch.object(ssrf_guard.socket, "getaddrinfo", side_effect=hang):
            start = time.monotonic()
            with pytest.raises(UnresolvableHostError, match="timed out"):
                validate_and_pin(
                    "https://hang.example/p", resolver=threads, timeout=0.2
                )
            assert time.monotonic() - start < 1.0
            with pytest.raises(HostLookupRunningError):
                validate_and_pin(
                    "https://HANG.example/q", resolver=threads, timeout=0.2
                )
            assert threads.running == 1
            release.set()
            deadline = time.monotonic() + 2
            while threads.running and time.monotonic() < deadline:
                time.sleep(0.01)  # poll for the thread to return
        assert threads.running == 0

    def test_timed_lookup_pins_like_the_plain_one(self):
        with patch.object(
            ssrf_guard.socket, "getaddrinfo", return_value=_PUBLIC_ADDRINFO
        ):
            t = validate_and_pin(
                "https://hook.example/p", resolver=ResolverThreads(1), timeout=1.0
            )
        assert t.url == f"https://{_PUBLIC_IP}/p"

    def test_pins_the_first_ipv4_answer(self):
        both = _addrinfo(_PUBLIC_V6) + _PUBLIC_ADDRINFO
        with patch.object(ssrf_guard.socket, "getaddrinfo", return_value=both):
            assert validate_and_pin("https://hook.example/p").url == (
                f"https://{_PUBLIC_IP}/p"
            )


# ---------------------------------------------------------------------------
# Integration: delivery re-resolves, pins, and refuses rebind targets
# ---------------------------------------------------------------------------


class TestDeliverySsrf(TestTemplate):
    def _seed(self, host: str = "hook.partner.example", *, subscribe_addrinfo=None):
        """Subscribe (validating against a public address) and enqueue one event."""
        addrinfo = (
            subscribe_addrinfo if subscribe_addrinfo is not None else _PUBLIC_ADDRINFO
        )
        with patch("services.webhooks_svc.socket.getaddrinfo", return_value=addrinfo):
            res = webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url=f"https://{host}/hook")
            )
        with db_engine.use_db_session() as session:
            enqueue_event(
                session, user_id="u1", event_type="gmail.message.new", payload={"x": 1}
            )
            session.commit()
        return res

    def _deliver(self, handler, delivery_addrinfo):
        """Drain the outbox with delivery-time DNS + HTTP client mocked."""
        transport = httpx.MockTransport(handler)
        real_client = httpx.Client

        def factory(*_a, **_k):
            return real_client(transport=transport)

        with ExitStack() as stack:
            # Force production posture so loopback is NOT waved through by the
            # dev allowance - the reject assertions test the deployed control,
            # not a developer machine's DEV_ENV.
            stack.enter_context(patch.object(global_config, "DEV_ENV", "prod"))
            stack.enter_context(patch("services._ssrf_guard.httpx.Client", factory))
            stack.enter_context(
                patch(
                    "services._ssrf_guard.socket.getaddrinfo",
                    return_value=delivery_addrinfo,
                )
            )
            return drain_due_deliveries()

    @pytest.mark.parametrize("internal_ip", _INTERNAL_ADDRS)
    def test_delivery_refuses_when_host_rebinds_to_internal(self, internal_ip):
        # Validated public at subscribe time; flipped to an internal address
        # before delivery. The POST must never leave the process.
        with _patch_db(), _plaintext_encryption():
            self._seed()
            rec = _Recorder()
            counts = self._deliver(rec, _addrinfo(internal_ip))

            assert rec.requests == []  # nothing connected
            assert counts["retry"] == 1
            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "pending"
                assert d.attempts == 1
                assert "non-public" in (d.last_error or "")

    def test_delivery_rejects_when_any_answer_is_internal(self):
        # Split-horizon at delivery: [public, private] -> rejected wholesale.
        with _patch_db(), _plaintext_encryption():
            self._seed()
            rec = _Recorder()
            counts = self._deliver(rec, _PUBLIC_ADDRINFO + _addrinfo("169.254.169.254"))
            assert rec.requests == []
            assert counts["retry"] == 1

    def test_legit_public_endpoint_delivers_to_pinned_ip(self):
        # Happy path: delivery resolves public, pins to that IP, and the POST
        # carries the original hostname in Host (proving the pin, not a second
        # unguarded resolve of the name).
        with _patch_db(), _plaintext_encryption():
            self._seed()
            rec = _Recorder(httpx.Response(200))
            counts = self._deliver(rec, _PUBLIC_ADDRINFO)

            assert counts["sent"] == 1
            assert len(rec.requests) == 1
            req = rec.requests[0]
            assert req.url.host == _PUBLIC_IP  # socket went to the validated IP
            assert req.headers["host"] == "hook.partner.example"
            with db_engine.use_db_session() as session:
                assert session.query(WebhookDelivery).one().status == "succeeded"

    def test_redirect_to_metadata_is_not_followed(self):
        # A subscriber that 302s toward the metadata IP must not be chased:
        # follow_redirects=False turns the 3xx into a non-success failure and
        # only the first (validated, public) hop is ever requested.
        redirect = httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )
        with _patch_db(), _plaintext_encryption():
            self._seed()
            rec = _Recorder(redirect)
            counts = self._deliver(rec, _PUBLIC_ADDRINFO)

            assert counts["sent"] == 0
            assert counts["retry"] == 1
            # Exactly one request, to the public pin - never the metadata host.
            assert len(rec.requests) == 1
            assert rec.requests[0].url.host == _PUBLIC_IP
