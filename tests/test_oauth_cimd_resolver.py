"""The CIMD resolver's fetch machinery against the threats it exists to contain.

Each test names the attack: slow targets filling the fetch cap, a burst of
resolves of one slow id, DNS that never answers, known ids sign-ins buy, and
tasks outliving the app. The network is a fake (``tests/cimd_fakes.py``) so the
tests control exactly when a fetch finishes; the deadline tests at the end use
the real fetch against ``tests/stall_server.py``. No test sleeps for a fixed
time. Cache tiers and lifetimes are ``tests/test_oauth_cimd_cache.py``.
"""

import socket
import threading
import time
from unittest.mock import patch

import anyio
import anyio.to_thread
import pytest

from api_server.oauth import cimd, cimd_cache
from api_server.oauth.cimd_document import (
    ClientMetadataError,
    FetchedDocument,
    TransientFetchError,
)
from services._ssrf_guard import (
    PinnedTarget,
    ResolverBusyError,
    ResolverThreads,
    validate_and_pin_async,
)
from tests.cimd_fakes import (
    DEADLINE,
    KNOWN,
    LIMIT,
    USER,
    CimdCase,
    assert_clean,
    cid,
    settled,
    small_resolver,
)
from tests.oauth_server_fixture import client_document
from tests.stall_server import StallServer
from tests.test_template import TestTemplate


class TestFetchCapacity(CimdCase):
    @pytest.mark.anyio
    async def test_slow_targets_cannot_lock_out_a_known_client(self):
        """F1: the cap is global, so distinct attacker targets fill it; known
        clients have their own budget, and busy is never cached."""
        cimd.mark_known(KNOWN, USER)  # holds a token, but no record yet
        async with anyio.create_task_group() as tg:
            for n in range(3):
                self.net.hold[cid(n)] = anyio.Event()
                tg.start_soon(self.expect_error, cid(n))
            await anyio.wait_all_tasks_blocked()
            assert cimd.snapshot().fetches == 3

            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            with pytest.raises(ClientMetadataError, match="too many"):
                await cimd.resolve_client(cid("new"))
            assert self.net.calls[cid("new")] == 0
            state = cimd.snapshot()
            assert cid("new") not in state.failed + state.backing_off

            for n in range(3):
                self.net.answer[cid(n)] = TransientFetchError("503")
                self.net.hold[cid(n)].set()
        assert_clean()
        assert (await cimd.resolve_client(cid("new"))).client_id == cid("new")

    @pytest.mark.anyio
    async def test_a_burst_of_one_slow_id_is_one_fetch_and_no_threads(self):
        """F2: 100 resolves of one slow id cost one fetch and no worker thread."""
        slow = cid("slow")
        self.net.hold[slow] = anyio.Event()
        results: list[str] = []

        async def resolve() -> None:
            results.append((await cimd.resolve_client(slow)).client_id)

        limiter = anyio.to_thread.current_default_thread_limiter()
        with patch.object(
            anyio.to_thread, "run_sync", side_effect=AssertionError("thread used")
        ):
            async with anyio.create_task_group() as tg:
                for _ in range(100):
                    tg.start_soon(resolve)
                await anyio.wait_all_tasks_blocked()
                assert self.net.calls[slow] == 1
                assert limiter.borrowed_tokens == 0
                assert cimd.snapshot().fetches == 1
                self.net.hold[slow].set()
        assert results == [slow] * 100
        assert_clean()

    @pytest.mark.anyio
    async def test_leader_failure_reaches_every_waiter(self):
        slow = cid("slow")
        self.net.hold[slow] = anyio.Event()
        self.net.answer[slow] = ClientMetadataError("gone for good")
        errors: list[str] = []

        async def resolve() -> None:
            try:
                await cimd.resolve_client(slow)
            except ClientMetadataError as exc:
                errors.append(str(exc))

        async with anyio.create_task_group() as tg:
            for _ in range(10):
                tg.start_soon(resolve)
            await anyio.wait_all_tasks_blocked()
            self.net.hold[slow].set()
        assert errors == ["gone for good"] * 10
        assert self.net.calls[slow] == 1
        assert_clean()


class TestCleanup(CimdCase):
    @pytest.mark.anyio
    async def test_nothing_is_left_after_success_or_error(self):
        await cimd.resolve_client(cid(1))
        self.net.answer[cid(2)] = ClientMetadataError("bad")
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(cid(2))
        assert_clean()

    @pytest.mark.anyio
    async def test_a_cancelled_caller_does_not_abort_the_shared_fetch(self):
        slow = cid("slow")
        self.net.hold[slow] = anyio.Event()
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as tg:
                tg.start_soon(cimd.resolve_client, slow)
                await anyio.wait_all_tasks_blocked()
                scope.cancel()
        assert cimd.snapshot().in_flight == (slow,)
        self.net.hold[slow].set()
        await settled(slow)
        assert_clean()
        assert slow in cimd.snapshot().cached  # the fetch finished anyway

    @pytest.mark.anyio
    async def test_a_fetch_past_the_deadline_releases_everything(self):
        slow = cid("slow")
        self.net.hold[slow] = anyio.Event()  # never set
        start = time.monotonic()
        with pytest.raises(ClientMetadataError, match="too long"):
            await cimd.resolve_client(slow)
        assert time.monotonic() - start < LIMIT
        assert_clean()
        assert slow not in cimd.snapshot().failed  # a timeout is not cached

    @pytest.mark.anyio
    async def test_a_crashing_fetch_is_contained(self):
        """N5: a bug in the fetch fails that resolve, not the task group."""
        self.net.answer[cid(1)] = RuntimeError("bug")
        with (
            patch.object(cimd.log, "exception") as logged,
            pytest.raises(ClientMetadataError, match="too long"),
        ):
            await cimd.resolve_client(cid(1))
        logged.assert_called_once()
        assert_clean()
        assert (await cimd.resolve_client(cid(2))).client_id == cid(2)

    @pytest.mark.anyio
    async def test_clear_cache_resets_flights_and_budgets(self):
        slow = cid("slow")
        self.net.hold[slow] = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(cimd.resolve_client, slow)
            await anyio.wait_all_tasks_blocked()
            cimd.mark_known(KNOWN, USER)
            cimd.clear_cache()  # the running fetch belongs to the old state
            assert_clean()
            assert cimd.snapshot().known == ()
            self.net.hold[slow].set()


class TestLifespan(TestTemplate):
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture(autouse=True)
    def _fakes(self):
        self.held = anyio.Event()

        async def fetch(target: PinnedTarget, **_):
            await self.held.wait()
            raise AssertionError("never answered")

        async def pin(client_id: str, **_) -> PinnedTarget:
            return PinnedTarget(url=client_id)

        with (
            patch.object(cimd, "_resolver", small_resolver()),
            patch.object(cimd, "validate_and_pin_async", side_effect=pin),
            patch.object(cimd, "fetch_document", side_effect=fetch),
        ):
            yield

    @pytest.mark.anyio
    async def test_shutdown_cancels_running_fetches(self):
        """N4: fetch tasks belong to the lifespan and end with it."""
        errors: list[str] = []

        async def resolve() -> None:
            try:
                await cimd.resolve_client(cid(1))
            except ClientMetadataError as exc:
                errors.append(str(exc))

        async with anyio.create_task_group() as outer:
            async with cimd.lifespan():
                outer.start_soon(resolve)
                await anyio.wait_all_tasks_blocked()
                assert cimd.snapshot().in_flight == (cid(1),)
            assert_clean()  # cancelled at shutdown, not left running
        assert errors == ["client metadata document took too long"]
        assert cimd._task_group is None

    @pytest.mark.anyio
    async def test_without_a_lifespan_the_caller_runs_the_fetch(self):
        self.held.set()
        with (
            patch.object(
                cimd,
                "fetch_document",
                side_effect=TransientFetchError("HTTP 503"),
            ),
            pytest.raises(ClientMetadataError, match="503"),
        ):
            await cimd.resolve_client(cid(1))
        assert_clean()


class TestHungDns(CimdCase):
    def _real_dns(self, hang_hosts: set[str], release: threading.Event):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        def getaddrinfo(host, *_args, **_kwargs):
            if host in hang_hosts:
                release.wait(10)
                raise socket.gaierror("gave up")
            return public

        return (
            # The real resolve-and-pin, in place of the fake from CimdCase.
            patch.object(cimd, "validate_and_pin_async", validate_and_pin_async),
            patch("services._ssrf_guard.socket.getaddrinfo", side_effect=getaddrinfo),
        )

    async def _threads_drain(self) -> None:
        with anyio.fail_after(LIMIT):  # poll for the threads to return
            while cimd.snapshot().resolver_threads:
                await anyio.sleep(0.01)

    @pytest.mark.anyio
    async def test_dns_that_never_answers_is_cut_off_and_frees_the_slot(self):
        """F3: the deadline covers DNS; a hung lookup keeps its (capped)
        thread but not the fetch slot."""
        release = threading.Event()
        pin, dns = self._real_dns({"cdns.test.example"}, release)
        with pin, dns:
            start = time.monotonic()
            with pytest.raises(ClientMetadataError, match="too long"):
                await cimd.resolve_client(cid("dns"))
            assert time.monotonic() - start < LIMIT
            assert_clean()
            assert cimd.snapshot().resolver_threads == 1  # still stuck
            release.set()
            await self._threads_drain()

    @pytest.mark.anyio
    async def test_one_host_never_holds_two_resolver_threads(self):
        """N1(a): past its backoff, an id whose lookup still hangs is refused
        a second thread."""
        release = threading.Event()
        pin, dns = self._real_dns({"cdns.test.example"}, release)
        with pin, dns:
            await self.expect_error(cid("dns"))
            self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
            with pytest.raises(ClientMetadataError, match="too many"):
                await cimd.resolve_client(cid("dns"))
            assert cimd.snapshot().resolver_threads == 1
            release.set()
            await self._threads_drain()

    @pytest.mark.anyio
    async def test_rotating_known_ids_cannot_take_a_known_client_offline(self):
        """H1 / L1: one account with many of its own client ids, every one
        re-vouched on each token use, all with hanging DNS, against a real
        known client whose record has gone past its stale bound."""
        release = threading.Event()
        evil = [f"https://a{n}.evil.example/m.json" for n in range(20)]
        hosts = {f"a{n}.evil.example" for n in range(20)}
        # As few known resolver threads as the attacker can hold, so the
        # legitimate refresh really does find them all taken.
        threads = ResolverThreads(cimd_cache.NEW_VOUCHES_PER_DAY)
        cimd.mark_known(KNOWN, USER)
        fetch = patch.object(
            cimd,
            "fetch_document",
            return_value=FetchedDocument(client_document(client_id=KNOWN), None),
        )
        pin, dns = self._real_dns(hosts, release)
        with (
            patch.object(cimd._resolver.known_budget, "resolver", threads),
            pin,
            dns,
            fetch,
        ):
            await cimd.resolve_client(KNOWN)
            for _ in range(3):
                for client_id in evil:
                    for vouched in evil:  # token use re-vouches them all
                        cimd.mark_known(vouched, "user_attacker")
                    with pytest.raises(ClientMetadataError):
                        await cimd.resolve_client(client_id)
                self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
            state = cimd.snapshot()
            assert len(state.known) == 1 + cimd_cache.NEW_VOUCHES_PER_DAY
            assert threads.running == cimd_cache.NEW_VOUCHES_PER_DAY  # all
            self.clock.now += (
                cimd_cache.DEFAULT_FRESH_SECONDS + cimd_cache.MAX_STALE_SECONDS
            )
            for _ in range(3):  # past stale_until, refreshes refused
                assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
                await settled(KNOWN)
            release.set()
            await self._threads_drain()
            self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            await settled(KNOWN)  # the refresh now succeeds
        assert KNOWN in cimd.snapshot().protected

    @pytest.mark.anyio
    async def test_resolver_threads_refuse_when_full(self):
        threads = ResolverThreads(0)
        with pytest.raises(ResolverBusyError):
            await threads.getaddrinfo("example.com", 443)


class TestRealFetchDeadline(TestTemplate):
    """The deadline holds against servers that stall mid-response."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture(autouse=True)
    def _server(self):
        self.stall = StallServer()
        with (
            patch.object(cimd, "_resolver", small_resolver()),
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", DEADLINE),
            patch.object(cimd, "validate_and_pin_async", side_effect=self._pin),
        ):
            yield
        self.stall.close()

    async def _pin(self, client_id: str, **_) -> PinnedTarget:
        # https://client.test.example/<path> -> the loopback server's <path>
        return PinnedTarget(url=self.stall.url("/" + client_id.split("/", 3)[3]))

    @pytest.mark.anyio
    @pytest.mark.parametrize("mode", ["flood", "header", "trailer", "body"])
    async def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        with pytest.raises(ClientMetadataError, match="too long"):
            await cimd.resolve_client(f"https://client.test.example/{mode}.json")
        assert time.monotonic() - start < LIMIT
        assert_clean()

    @pytest.mark.anyio
    async def test_a_served_document_resolves(self):
        client_id = "https://client.test.example/ok.json"
        self.stall.documents["/ok.json"] = client_document(client_id=client_id)
        async with cimd.lifespan():
            assert (await cimd.resolve_client(client_id)).client_id == client_id
