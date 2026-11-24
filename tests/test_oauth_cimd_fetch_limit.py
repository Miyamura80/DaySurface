"""CIMD fetch caps, stale serving and single flight.

Slow fetches are held open with events and barriers, not sleeps, so each test
decides exactly when a fetch finishes.
"""

import contextlib
import threading
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest

from api_server.oauth import cimd
from services._ssrf_guard import (
    FetchLimitBusyError,
    FetchLimiter,
    PinnedTarget,
    target_key,
)
from tests.oauth_server_fixture import client_document
from tests.test_template import TestTemplate

_JOIN = 5.0  # generous upper bound; a passing test never waits this long
_PER_TARGET = cimd._MAX_FETCHES_PER_TARGET
_TARGETS_TO_FILL = cimd._MAX_CONCURRENT_FETCHES // _PER_TARGET
KNOWN = "https://known.test.example/meta.json"


class TestFetchLimiter(TestTemplate):
    def test_per_target_cap_refuses_at_once(self):
        limiter = FetchLimiter(total=4, per_target=2)
        with limiter.hold("203.0.113.1"), limiter.hold("203.0.113.2"):
            with pytest.raises(FetchLimitBusyError), limiter.hold("203.0.113.3"):
                pass
            with limiter.hold("198.51.100.1"):
                assert limiter.snapshot().per_target == {
                    "203.0.113.0/24": 2,
                    "198.51.100.0/24": 1,
                }
        assert limiter.snapshot().running == 0

    def test_global_cap_refuses_at_once(self):
        limiter = FetchLimiter(total=2, per_target=2)
        with (
            limiter.hold("203.0.113.1"),
            limiter.hold("198.51.100.1"),
            pytest.raises(FetchLimitBusyError),
            limiter.hold("192.0.2.1"),
        ):
            pass

    def test_targets_are_network_prefixes(self):
        assert target_key("203.0.113.5") == target_key("203.0.113.250")
        assert target_key("203.0.113.5") != target_key("203.0.114.5")
        assert target_key("2001:db8:0:1::5") == "2001:db8:0:1::/64"
        assert target_key("2001:db8:0:1:ffff::1") == target_key("2001:db8:0:1::5")
        assert target_key("::ffff:203.0.113.9") == "203.0.113.0/24"

    def test_slot_is_released_when_the_block_raises(self):
        limiter = FetchLimiter(total=1, per_target=1)
        with pytest.raises(KeyError), limiter.hold("203.0.113.1"):
            raise KeyError
        assert limiter.snapshot().running == 0
        assert limiter.snapshot().per_target == {}


class _Interrupted(BaseException):
    """Stands in for KeyboardInterrupt / SystemExit escaping a fetch."""


class TestResolveClient(TestTemplate):
    """``resolve_client`` with DNS and the HTTP fetch replaced.

    ``self.pins`` maps a hostname to the address it "resolves" to. A fetch for a
    host listed in ``self.slow`` blocks until ``self.release`` is set.
    """

    def setup_method(self):
        cimd.clear_cache()
        self.pins = {"known.test.example": "192.0.2.10"}
        self.slow: set[str] = set()
        self.entered: threading.Barrier | None = None
        self.release = threading.Event()
        self.fetched: list[str] = []
        self.on_fetch: Callable[[str], object] | None = None
        self.threads: list[threading.Thread] = []
        self._patches = contextlib.ExitStack()
        for target, value in (
            ("_fetch_limiter", self._limiter()),
            ("validate_and_pin", MagicMock(side_effect=self._pin)),
            ("_fetch_document", MagicMock(side_effect=self._fetch)),
        ):
            self._patches.enter_context(patch.object(cimd, target, value))

    def teardown_method(self):
        try:
            self._finish()
            assert not any(thread.is_alive() for thread in self.threads)
            state = cimd.snapshot()
            assert state.in_flight == ()
            assert state.fetches.running == 0
            assert state.fetches.per_target == {}
        finally:
            self._patches.close()
            cimd.clear_cache()

    @staticmethod
    def _limiter() -> FetchLimiter:
        return FetchLimiter(total=cimd._MAX_CONCURRENT_FETCHES, per_target=_PER_TARGET)

    def _pin(self, client_id: str) -> PinnedTarget:
        parts = urlsplit(client_id)
        host = parts.hostname or ""
        return PinnedTarget(
            url=f"https://{self.pins[host]}{parts.path}", headers={"Host": host}
        )

    def _fetch(self, target: PinnedTarget, deadline: float) -> bytes:
        host = target.headers["Host"]
        client_id = f"https://{host}{urlsplit(target.url).path}"
        self.fetched.append(client_id)
        if host in self.slow:
            assert self.entered is not None
            self.entered.wait(_JOIN)
            assert self.release.wait(_JOIN)
        if self.on_fetch is not None:
            self.on_fetch(client_id)
        return client_document(client_id=client_id)

    def _in_thread(self, client_id: str, results: list) -> None:
        def run() -> None:
            try:
                results.append(cimd.resolve_client(client_id))
            except (cimd.ClientMetadataError, _Interrupted) as exc:
                results.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)

    def _hold_slots(self, client_ids: list[str]) -> list:
        """Start a slow resolve per id and return once each holds a slot."""
        self.entered = threading.Barrier(len(client_ids) + 1)
        results: list = []
        for client_id in client_ids:
            self._in_thread(client_id, results)
        self.entered.wait(_JOIN)
        return results

    def _finish(self) -> None:
        self.release.set()
        for thread in self.threads:
            thread.join(_JOIN)

    def test_slow_targets_lock_out_only_clients_never_seen(self):
        with patch.object(cimd, "_CACHE_SECONDS", 0):  # cached, already expired
            known = cimd.resolve_client(KNOWN)
        attackers = []
        for n in range(_TARGETS_TO_FILL):
            host = f"slow-{n}.attacker.example"
            self.pins[host] = f"198.51.{n}.10"
            self.slow.add(host)
            attackers += [f"https://{host}/{i}.json" for i in range(_PER_TARGET)]
        self._hold_slots(attackers)
        assert cimd.snapshot().fetches.running == cimd._MAX_CONCURRENT_FETCHES

        assert cimd.resolve_client(KNOWN) is known  # served stale
        new = "https://new.test.example/meta.json"
        self.pins["new.test.example"] = "192.0.2.20"
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.resolve_client(new)
        assert new not in cimd.snapshot().failed  # busy is not a failure

        self._finish()
        assert cimd.resolve_client(new).client_id == new

    def test_stale_record_is_not_served_past_its_limit(self):
        with (
            patch.object(cimd, "_CACHE_SECONDS", 0),
            patch.object(cimd, "_MAX_STALE_SECONDS", 0),
        ):
            cimd.resolve_client(KNOWN)
        self.slow.add("known.test.example")
        self._hold_slots([f"https://known.test.example/{i}.json" for i in range(2)])
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.resolve_client(KNOWN)

    def test_hosts_in_one_network_share_a_cap(self):
        self.pins |= {
            "a.example": "203.0.113.5",
            "b.example": "203.0.113.77",
            "c.example": "203.0.113.200",
            "d.example": "203.0.114.1",
        }
        self.slow |= {"a.example", "b.example"}
        self._hold_slots(["https://a.example/meta.json", "https://b.example/meta.json"])
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.resolve_client("https://c.example/meta.json")
        assert cimd.resolve_client("https://d.example/meta.json")

    def test_concurrent_resolves_share_one_fetch(self):
        callers = 5
        arrived = threading.Barrier(callers)
        real_wait = cimd._wait_for

        def wait_for(flight, deadline):
            arrived.wait(_JOIN)  # every follower has joined the flight
            return real_wait(flight, deadline)

        self.on_fetch = lambda _client_id: arrived.wait(_JOIN)
        results: list = []
        with patch.object(cimd, "_wait_for", side_effect=wait_for):
            for _ in range(callers):
                self._in_thread(KNOWN, results)
            self._finish()
        assert self.fetched == [KNOWN]
        assert len(results) == callers
        assert all(result is results[0] for result in results)
        assert results[0].client_id == KNOWN

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (cimd.ClientMetadataError("document is broken"), "document is broken"),
            (_Interrupted(), "fetch failed"),
        ],
    )
    def test_leader_failure_reaches_every_follower(self, error, message):
        callers = 4
        arrived = threading.Barrier(callers)
        real_wait = cimd._wait_for

        def wait_for(flight, deadline):
            arrived.wait(_JOIN)
            return real_wait(flight, deadline)

        def fail(_client_id: str) -> None:
            arrived.wait(_JOIN)
            raise error

        self.on_fetch = fail
        results: list = []
        with patch.object(cimd, "_wait_for", side_effect=wait_for):
            for _ in range(callers):
                self._in_thread(KNOWN, results)
            self._finish()
        assert self.fetched == [KNOWN]
        assert len(results) == callers
        leader = [r for r in results if r is error]
        followers = [r for r in results if r is not error]
        assert len(leader) == 1
        assert all(isinstance(r, cimd.ClientMetadataError) for r in followers)
        assert all(message in str(r) for r in followers)

    def test_follower_gives_up_at_its_deadline(self):
        self.slow.add("known.test.example")
        self._hold_slots([KNOWN])
        with (
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", 0.05),
            pytest.raises(cimd.ClientMetadataError, match="too long"),
        ):
            cimd.resolve_client(KNOWN)
        assert cimd.snapshot().in_flight == (KNOWN,)  # the leader is unaffected

    def test_fetch_deadline_runs_from_the_start_of_the_resolve(self):
        clock = SimpleNamespace(now=1000.0)
        pin = self._pin
        deadlines: list[float] = []

        def slow_dns(client_id: str) -> PinnedTarget:
            clock.now += 2.0
            return pin(client_id)

        def fetch(target: PinnedTarget, deadline: float) -> bytes:
            deadlines.append(deadline)
            return client_document(client_id=KNOWN)

        with (
            patch.object(cimd, "time", SimpleNamespace(monotonic=lambda: clock.now)),
            patch.object(cimd, "validate_and_pin", side_effect=slow_dns),
            patch.object(cimd, "_fetch_document", side_effect=fetch),
        ):
            cimd.resolve_client(KNOWN)
        assert deadlines == [1000.0 + cimd._FETCH_DEADLINE_SECONDS]

    def test_dns_that_uses_up_the_deadline_skips_the_fetch(self):
        clock = SimpleNamespace(now=1000.0)
        pin = self._pin

        def slow_dns(client_id: str) -> PinnedTarget:
            clock.now += cimd._FETCH_DEADLINE_SECONDS
            return pin(client_id)

        with (
            patch.object(cimd, "time", SimpleNamespace(monotonic=lambda: clock.now)),
            patch.object(cimd, "validate_and_pin", side_effect=slow_dns),
            pytest.raises(cimd.ClientMetadataError, match="too long"),
        ):
            cimd.resolve_client(KNOWN)
        assert self.fetched == []

    def test_unexpected_error_leaves_no_state_behind(self):
        def explode(_client_id: str) -> None:
            raise RuntimeError("bug")

        self.on_fetch = explode
        with pytest.raises(RuntimeError):
            cimd.resolve_client(KNOWN)
        # teardown_method asserts no flight or slot is left; the next try refetches.
        self.on_fetch = None
        assert cimd.resolve_client(KNOWN).client_id == KNOWN

    def test_cache_evicts_the_least_recently_used(self):
        self.pins |= {"a.example": "203.0.113.1", "b.example": "203.0.113.2"}
        a, b = "https://a.example/m.json", "https://b.example/m.json"
        with patch.object(cimd, "_MAX_CACHE_ENTRIES", 2):
            cimd.resolve_client(a)
            cimd.resolve_client(b)
            cimd.resolve_client(a)  # a hit makes a the most recent
            cimd.resolve_client(KNOWN)
        assert cimd.snapshot().cached == (a, KNOWN)

    def test_fresh_cache_hit_does_not_refetch(self):
        assert cimd.resolve_client(KNOWN) is cimd.resolve_client(KNOWN)
        assert self.fetched == [KNOWN]
