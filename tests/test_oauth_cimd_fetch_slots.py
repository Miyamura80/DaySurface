"""CIMD fetch limits: one slow host cannot lock other clients out of signing in.

Blocking fetches are held open with events, not sleeps, so each test decides
exactly when a slot frees up.
"""

import contextlib
import threading
import time
from collections.abc import Callable
from unittest.mock import patch

import pytest

from api_server.oauth import cimd
from api_server.oauth.fetch_slots import FetchSlots, FetchSlotsBusyError
from tests.oauth_server_fixture import client_document
from tests.test_template import TestTemplate

_JOIN = 5.0  # generous upper bound; a passing test never waits this long


def _until(condition: Callable[[], bool]) -> None:
    """Wait for another thread to reach a state we cannot signal directly."""
    end = time.monotonic() + _JOIN
    while not condition():
        assert time.monotonic() < end, "timed out waiting for the slot state"
        threading.Event().wait(0.001)


class _Holders:
    """Threads that each take a slot for a host and keep it until released."""

    def __init__(self, slots: FetchSlots) -> None:
        self.slots = slots
        self.release = threading.Event()
        self.threads: list[threading.Thread] = []
        self.errors: list[BaseException] = []

    def start(self, host: str, count: int, timeout: float = 0) -> None:
        entered = threading.Barrier(count + 1) if timeout == 0 else None

        def run() -> None:
            try:
                with self.slots.hold(host, timeout=timeout):
                    if entered:
                        entered.wait(_JOIN)
                    assert self.release.wait(_JOIN)
            except FetchSlotsBusyError as exc:
                self.errors.append(exc)

        for _ in range(count):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            self.threads.append(thread)
        if entered:
            entered.wait(_JOIN)  # every holder is inside its slot

    def finish(self) -> None:
        self.release.set()
        for thread in self.threads:
            thread.join(_JOIN)
        assert not any(thread.is_alive() for thread in self.threads)


class TestFetchSlots(TestTemplate):
    @pytest.fixture
    def slots(self):
        return FetchSlots(total=4, per_host=2, max_waiting=4)

    def test_slow_host_does_not_block_another_host(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        try:
            with slots.hold("fast.example", timeout=0):
                assert slots.snapshot()["fast.example"] == (1, 0)
        finally:
            holders.finish()

    def test_per_host_cap_refuses_without_waiting(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        try:
            with (
                pytest.raises(FetchSlotsBusyError),
                slots.hold("slow.example", timeout=0),
            ):
                pass
        finally:
            holders.finish()

    def test_per_host_cap_queues_until_a_slot_frees(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        queued = _Holders(slots)
        queued.start("slow.example", 1, timeout=_JOIN)
        _until(lambda: slots.snapshot()["slow.example"] == (2, 1))
        holders.finish()  # frees the host's slots; the queued fetch gets one
        _until(lambda: slots.snapshot().get("slow.example") == (1, 0))
        queued.finish()
        assert not queued.errors

    def test_per_host_queue_is_bounded(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        queued = _Holders(slots)
        queued.start("slow.example", 2, timeout=_JOIN)
        _until(lambda: slots.snapshot()["slow.example"] == (2, 2))
        try:
            start = time.monotonic()
            with (
                pytest.raises(FetchSlotsBusyError),
                slots.hold("slow.example", timeout=_JOIN),
            ):
                pass
            assert time.monotonic() - start < 1.0  # refused, not queued
        finally:
            holders.finish()
            queued.finish()

    def test_queued_fetch_gives_up_after_its_timeout(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        try:
            with (
                pytest.raises(FetchSlotsBusyError),
                slots.hold("slow.example", timeout=0.05),
            ):
                pass
        finally:
            holders.finish()

    def test_global_cap_and_queue_bound_many_hosts(self):
        slots = FetchSlots(total=2, per_host=2, max_waiting=1)
        holders = _Holders(slots)
        holders.start("a.example", 1)
        holders.start("b.example", 1)
        queued = _Holders(slots)
        queued.start("c.example", 1, timeout=_JOIN)
        _until(lambda: slots.snapshot().get("c.example") == (0, 1))
        try:
            with (
                pytest.raises(FetchSlotsBusyError),
                slots.hold("d.example", timeout=_JOIN),
            ):
                pass
        finally:
            holders.finish()
            queued.finish()
        assert not queued.errors

    def test_bookkeeping_is_cleaned_up(self, slots):
        holders = _Holders(slots)
        holders.start("slow.example", 2)
        with pytest.raises(FetchSlotsBusyError), slots.hold("slow.example", timeout=0):
            pass
        with (
            pytest.raises(FetchSlotsBusyError),
            slots.hold("slow.example", timeout=0.01),
        ):
            pass
        holders.finish()
        with pytest.raises(RuntimeError), slots.hold("boom.example", timeout=0):
            raise RuntimeError("the fetch failed")
        for n in range(20):
            with slots.hold(f"host-{n}.example", timeout=0):
                pass
        assert slots.snapshot() == {}
        assert (slots._running, slots._waiting) == (0, 0)


class TestResolveClientLimits(TestTemplate):
    """The limits as resolve_client applies them, with the fetch faked."""

    def setup_method(self):
        cimd.clear_cache()
        self.release = threading.Event()
        self.slow_entered = threading.Barrier(cimd._MAX_FETCHES_PER_HOST + 1)
        self.fetches: list[str] = []
        self.slots = FetchSlots(
            total=cimd._MAX_CONCURRENT_FETCHES,
            per_host=cimd._MAX_FETCHES_PER_HOST,
            max_waiting=cimd._MAX_QUEUED_FETCHES,
        )
        self._patches = contextlib.ExitStack()
        self._patches.enter_context(patch.object(cimd, "_fetch_slots", self.slots))
        self._patches.enter_context(patch.object(cimd, "_SLOT_WAIT_SECONDS", 0.05))
        self._patches.enter_context(
            patch.object(cimd, "_fetch_document", side_effect=self._fetch)
        )

    def teardown_method(self):
        self.release.set()
        self._patches.close()
        cimd.clear_cache()

    def _fetch(self, client_id: str, deadline: float | None = None) -> bytes:
        self.fetches.append(client_id)
        if "slow.example" in client_id:
            self.slow_entered.wait(_JOIN)
            assert self.release.wait(_JOIN)
            raise cimd.ClientMetadataError("timed out")
        return client_document(client_id=client_id)

    def _hold_slow_host(self) -> list[threading.Thread]:
        def run(n: int) -> None:
            with contextlib.suppress(cimd.ClientMetadataError):
                cimd.resolve_client(f"https://slow.example/client-{n}.json")

        threads = [
            threading.Thread(target=run, args=(n,), daemon=True)
            for n in range(cimd._MAX_FETCHES_PER_HOST)
        ]
        for thread in threads:
            thread.start()
        self.slow_entered.wait(_JOIN)  # the slow host holds all its slots
        return threads

    def _finish(self, threads: list[threading.Thread]) -> None:
        self.release.set()
        for thread in threads:
            thread.join(_JOIN)
        assert self.slots.snapshot() == {}

    def test_slow_host_does_not_lock_out_other_clients(self):
        threads = self._hold_slow_host()
        try:
            good = "https://good.example/client.json"
            assert cimd.resolve_client(good).client_id == good
        finally:
            self._finish(threads)

    def test_busy_host_is_refused_and_not_cached_as_a_failure(self):
        threads = self._hold_slow_host()
        busy = "https://slow.example/another.json"
        try:
            with pytest.raises(cimd.ClientMetadataError, match="too many"):
                cimd.resolve_client(busy)
            assert busy not in cimd._failures
        finally:
            self._finish(threads)

    def test_queued_request_reuses_the_fetch_it_waited_behind(self):
        client_id = "https://shared.example/client.json"
        slots = FetchSlots(total=8, per_host=1, max_waiting=8)
        first_inside = threading.Event()
        let_first_finish = threading.Event()

        def fetch(cid: str, deadline: float | None = None) -> bytes:
            self.fetches.append(cid)
            first_inside.set()
            assert let_first_finish.wait(_JOIN)
            return client_document(client_id=cid)

        results: list[str] = []

        def resolve() -> None:
            results.append(cimd.resolve_client(client_id).client_id)

        with (
            patch.object(cimd, "_fetch_slots", slots),
            patch.object(cimd, "_SLOT_WAIT_SECONDS", _JOIN),
            patch.object(cimd, "_fetch_document", side_effect=fetch),
        ):
            first = threading.Thread(target=resolve, daemon=True)
            first.start()
            assert first_inside.wait(_JOIN)
            second = threading.Thread(target=resolve, daemon=True)
            second.start()
            _until(lambda: slots.snapshot()["shared.example"] == (1, 1))
            let_first_finish.set()
            first.join(_JOIN)
            second.join(_JOIN)
        assert results == [client_id, client_id]
        assert self.fetches == [client_id]  # the queued request hit the cache
        assert slots.snapshot() == {}
