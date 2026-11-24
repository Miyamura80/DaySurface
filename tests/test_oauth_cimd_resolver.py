"""The CIMD resolver against the threats it exists to contain.

Each test names the attack: slow targets filling the fetch cap, a burst of
resolves of one slow id, DNS that never answers, attacker documents flooding
the cache, a document stretching its own lifetime, and a known client's host
failing. The network is a fake (``_Net``) so the tests control exactly when a
fetch finishes; the deadline tests at the end use the real fetch against
``tests/stall_server.py``. No test sleeps for a fixed time.
"""

import socket
import threading
import time
from collections import Counter
from unittest.mock import patch

import anyio
import anyio.to_thread
import pytest

from api_server.oauth import cimd
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
from tests.oauth_server_fixture import CLIENT_ID, OAuthServerCase, client_document
from tests.stall_server import StallServer
from tests.test_template import TestTemplate

KNOWN = "https://known.test.example/meta.json"
_DEADLINE = 0.5
_LIMIT = 1.5  # what a resolve may take: deadline, grace and slack


def _id(n: int | str) -> str:
    return f"https://c{n}.test.example/meta.json"


class _Net:
    """Fake pin + fetch. ``hold`` gates a client_id; ``answer`` picks the reply."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.hold: dict[str, anyio.Event] = {}
        self.answer: dict[str, bytes | Exception | tuple[bytes, str]] = {}

    async def pin(self, client_id: str, **_) -> PinnedTarget:
        return PinnedTarget(url=client_id)

    async def fetch(self, target: PinnedTarget, **_) -> FetchedDocument:
        client_id = target.url
        self.calls[client_id] += 1
        if (gate := self.hold.get(client_id)) is not None:
            await gate.wait()
        answer = self.answer.get(client_id, client_document(client_id=client_id))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            return FetchedDocument(*answer)
        assert isinstance(answer, bytes)
        return FetchedDocument(answer, None)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _settled(client_id: str) -> None:
    """Wait for the fetch task of ``client_id``, if one is running."""
    flight = cimd._in_flight.get(client_id)
    if flight is not None:
        with anyio.fail_after(_LIMIT):
            await flight.done.wait()


def _assert_clean() -> None:
    state = cimd.snapshot()
    assert state.in_flight == ()
    assert state.fetches == 0
    assert state.known_fetches == 0
    assert all(task.done() for task in cimd._tasks)


class _Case(TestTemplate):
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture(autouse=True)
    def _fakes(self):
        cimd.clear_cache()
        self.net = _Net()
        self.clock = _Clock()
        with (
            patch.object(cimd, "validate_and_pin_async", side_effect=self.net.pin),
            patch.object(cimd, "fetch_document", side_effect=self.net.fetch),
            patch.object(cimd, "_clock", self.clock),
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", _DEADLINE),
            patch.object(cimd, "_budget", cimd._Budget(3, 3)),
            patch.object(cimd, "_known_budget", cimd._Budget(2, 2)),
        ):
            yield
        cimd.clear_cache()


class TestFetchCapacity(_Case):
    @pytest.mark.anyio
    async def test_slow_targets_cannot_lock_out_a_known_client(self):
        """F1: the cap is global, so distinct attacker targets fill it; known
        clients have their own budget, and busy is never cached."""
        cimd.mark_known(KNOWN)  # holds a token, but no record in this process
        async with anyio.create_task_group() as tg:
            for n in range(3):
                self.net.hold[_id(n)] = anyio.Event()
                tg.start_soon(self._expect_error, _id(n))
            await anyio.wait_all_tasks_blocked()
            assert cimd.snapshot().fetches == 3

            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            with pytest.raises(ClientMetadataError, match="too many"):
                await cimd.resolve_client(_id("new"))
            assert self.net.calls[_id("new")] == 0
            assert _id("new") not in cimd.snapshot().failed

            for n in range(3):
                self.net.answer[_id(n)] = TransientFetchError("503")
                self.net.hold[_id(n)].set()
        _assert_clean()
        assert (await cimd.resolve_client(_id("new"))).client_id == _id("new")

    async def _expect_error(self, client_id: str) -> None:
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(client_id)

    @pytest.mark.anyio
    async def test_a_burst_of_one_slow_id_is_one_fetch_and_no_threads(self):
        """F2: 100 resolves of one slow id cost one fetch and no worker thread."""
        slow = _id("slow")
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
        _assert_clean()

    @pytest.mark.anyio
    async def test_leader_failure_reaches_every_waiter(self):
        slow = _id("slow")
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
        _assert_clean()


class TestCleanup(_Case):
    @pytest.mark.anyio
    async def test_nothing_is_left_after_success_or_error(self):
        await cimd.resolve_client(_id(1))
        self.net.answer[_id(2)] = ClientMetadataError("bad")
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(_id(2))
        _assert_clean()

    @pytest.mark.anyio
    async def test_a_cancelled_caller_does_not_abort_the_shared_fetch(self):
        slow = _id("slow")
        self.net.hold[slow] = anyio.Event()
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as tg:
                tg.start_soon(cimd.resolve_client, slow)
                await anyio.wait_all_tasks_blocked()
                scope.cancel()
        assert cimd.snapshot().in_flight == (slow,)
        self.net.hold[slow].set()
        await _settled(slow)
        _assert_clean()
        assert slow in cimd.snapshot().cached  # the fetch finished anyway

    @pytest.mark.anyio
    async def test_a_cancelled_fetch_task_releases_everything(self):
        slow = _id("slow")
        self.net.hold[slow] = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._expect_error, slow)
            await anyio.wait_all_tasks_blocked()
            for task in list(cimd._tasks):
                task.cancel()
        _assert_clean()

    @pytest.mark.anyio
    async def test_a_fetch_past_the_deadline_releases_everything(self):
        slow = _id("slow")
        self.net.hold[slow] = anyio.Event()  # never set
        start = time.monotonic()
        with pytest.raises(ClientMetadataError, match="too long"):
            await cimd.resolve_client(slow)
        assert time.monotonic() - start < _LIMIT
        _assert_clean()
        assert slow not in cimd.snapshot().failed  # a timeout is not cached

    async def _expect_error(self, client_id: str) -> None:
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(client_id)


class TestHungDns(_Case):
    @pytest.mark.anyio
    async def test_dns_that_never_answers_is_cut_off_and_frees_the_slot(self):
        """F3: the deadline covers DNS, and a hung lookup holds a resolver
        thread (capped) but not the fetch slot."""
        release = threading.Event()

        def hang(*_args, **_kwargs):
            release.wait(10)
            raise socket.gaierror("gave up")

        budget = cimd._Budget(3, 1)
        with (
            patch.object(cimd, "_budget", budget),
            # The real resolve-and-pin, in place of the fake from _fakes.
            patch.object(cimd, "validate_and_pin_async", validate_and_pin_async),
            patch("services._ssrf_guard.socket.getaddrinfo", side_effect=hang),
        ):
            start = time.monotonic()
            with pytest.raises(ClientMetadataError, match="too long"):
                await cimd.resolve_client(_id("dns"))
            assert time.monotonic() - start < _LIMIT
            _assert_clean()
            assert budget.resolver.running == 1  # the thread is still stuck
            # It still counts: hung lookups cannot pile up past the cap.
            with pytest.raises(ClientMetadataError, match="too many"):
                await cimd.resolve_client(_id("dns2"))
            release.set()
            with anyio.fail_after(_LIMIT):  # poll for the thread to return
                while budget.resolver.running:
                    await anyio.sleep(0.01)
        _assert_clean()

    def test_resolver_threads_refuse_when_full(self):
        threads = ResolverThreads(0)
        with pytest.raises(ResolverBusyError):
            anyio.run(threads.getaddrinfo, "example.com", 443)


class TestCacheTiers(_Case):
    @pytest.mark.anyio
    async def test_unknown_ids_cannot_evict_a_known_client(self):
        """F4: the LRU is attacker-fillable; the protected tier is not."""
        with patch.object(cimd, "_MAX_CACHE_ENTRIES", 3):
            await cimd.resolve_client(KNOWN)
            cimd.mark_known(KNOWN)  # moves the record out of the LRU
            for n in range(10):
                await cimd.resolve_client(_id(n))
            state = cimd.snapshot()
            assert len(state.cached) == 3
            assert KNOWN in state.protected
            await cimd.resolve_client(KNOWN)
        assert self.net.calls[KNOWN] == 1  # served from the protected tier


class TestKnownByIssuance(OAuthServerCase):
    def test_a_completed_sign_in_protects_the_client(self):
        assert CLIENT_ID not in cimd.snapshot().known
        code, verifier = self._sign_in()
        assert CLIENT_ID in cimd.snapshot().known  # a code was issued
        assert CLIENT_ID in cimd.snapshot().protected
        cimd.clear_cache()
        assert self._exchange(code, verifier)["status"] == 200
        assert CLIENT_ID in cimd.snapshot().known  # so were tokens


class TestFreshness(_Case):
    @pytest.mark.anyio
    async def test_max_age_is_honoured(self):
        self.net.answer[_id(1)] = (client_document(client_id=_id(1)), "max-age=120")
        await cimd.resolve_client(_id(1))
        self.clock.now += 119
        await cimd.resolve_client(_id(1))
        assert self.net.calls[_id(1)] == 1
        self.clock.now += 2
        await cimd.resolve_client(_id(1))
        assert self.net.calls[_id(1)] == 2

    @pytest.mark.anyio
    async def test_max_age_is_clamped(self):
        self.net.answer[_id(1)] = (client_document(client_id=_id(1)), "max-age=0")
        self.net.answer[_id(2)] = (client_document(client_id=_id(2)), "max-age=9e9")
        self.net.answer[_id(3)] = (
            client_document(client_id=_id(3)),
            "max-age=99999999",
        )
        for n in (1, 2, 3):
            await cimd.resolve_client(_id(n))
        self.clock.now += cimd._MIN_FRESH_SECONDS - 1
        await cimd.resolve_client(_id(1))
        assert self.net.calls[_id(1)] == 1  # max-age=0 still gets the floor
        self.clock.now += cimd._MAX_FRESH_SECONDS
        for n in (1, 2, 3):
            await cimd.resolve_client(_id(n))
        assert [self.net.calls[_id(n)] for n in (1, 2, 3)] == [2, 2, 2]

    @pytest.mark.anyio
    async def test_no_store_is_never_served_stale(self):
        """F5: a document that forbids storing is not kept past freshness."""
        self.net.answer[_id(1)] = (client_document(client_id=_id(1)), "no-store")
        await cimd.resolve_client(_id(1))
        self.clock.now += cimd._MIN_FRESH_SECONDS + 1
        self.net.answer[_id(1)] = TransientFetchError("HTTP 503")
        with pytest.raises(ClientMetadataError, match="503"):
            await cimd.resolve_client(_id(1))

    @pytest.mark.anyio
    async def test_staleness_is_bounded(self):
        """F5: past freshness plus _MAX_STALE_SECONDS nothing is served."""
        await cimd.resolve_client(_id(1))
        self.net.answer[_id(1)] = TransientFetchError("HTTP 503")
        self.clock.now += cimd._DEFAULT_FRESH_SECONDS + cimd._MAX_STALE_SECONDS - 1
        assert (await cimd.resolve_client(_id(1))).client_id == _id(1)
        self.clock.now += 2
        with pytest.raises(ClientMetadataError, match="503"):
            await cimd.resolve_client(_id(1))

    @pytest.mark.anyio
    async def test_busy_serves_stale_only_within_the_bound(self):
        await cimd.resolve_client(_id(1))
        self.clock.now += cimd._DEFAULT_FRESH_SECONDS + 1
        with patch.object(cimd, "_budget", cimd._Budget(0, 0)):
            assert (await cimd.resolve_client(_id(1))).client_id == _id(1)
            self.clock.now += cimd._MAX_STALE_SECONDS
            with pytest.raises(ClientMetadataError, match="too many"):
                await cimd.resolve_client(_id(1))

    def test_cache_control_parsing(self):
        fresh, stale = cimd._lifetimes(None)
        assert (fresh, stale) == (cimd._DEFAULT_FRESH_SECONDS, cimd._MAX_STALE_SECONDS)
        assert cimd._lifetimes("public, max-age=600")[0] == 600
        assert cimd._lifetimes('max-age="600"')[0] == 600
        assert cimd._lifetimes("max-age=-5")[0] == cimd._DEFAULT_FRESH_SECONDS
        assert cimd._lifetimes("No-Store")[1] == 0
        assert cimd._lifetimes("max-age=600, no-cache")[1] == 0


class TestKnownClientErrors(_Case):
    async def _stale_known(self) -> None:
        cimd.mark_known(KNOWN)
        await cimd.resolve_client(KNOWN)
        self.clock.now += cimd._DEFAULT_FRESH_SECONDS + 1

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "error",
        [TransientFetchError("HTTP 503"), TransientFetchError("network")],
    )
    async def test_transient_error_from_a_known_host_serves_stale(self, error):
        """F6: a 5xx or network error keeps the known client signing in."""
        await self._stale_known()
        self.net.answer[KNOWN] = error
        for _ in range(3):
            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            await _settled(KNOWN)
        assert KNOWN not in cimd.snapshot().failed
        assert self.net.calls[KNOWN] == 4  # one background refresh per resolve

    @pytest.mark.anyio
    async def test_timeout_from_a_known_host_serves_stale(self):
        await self._stale_known()
        self.net.hold[KNOWN] = anyio.Event()  # never answers
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
        await _settled(KNOWN)
        assert KNOWN in cimd.snapshot().protected
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "answer",
        [
            ClientMetadataError("client metadata fetch returned HTTP 404"),
            b"not json",
            client_document(client_id=_id("other")),
        ],
    )
    async def test_definite_failure_drops_the_record(self, answer):
        """F6: a 4xx, invalid or mismatched document is an answer: no stale."""
        await self._stale_known()
        self.net.answer[KNOWN] = answer
        await cimd.resolve_client(KNOWN)  # stale, while the refresh runs
        await _settled(KNOWN)
        assert KNOWN not in cimd.snapshot().protected
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(KNOWN)

    @pytest.mark.anyio
    async def test_unknown_client_gets_stale_on_transient_error_only(self):
        await cimd.resolve_client(_id(1))
        await cimd.resolve_client(_id(2))
        self.clock.now += cimd._DEFAULT_FRESH_SECONDS + 1
        self.net.answer[_id(1)] = TransientFetchError("HTTP 502")
        self.net.answer[_id(2)] = ClientMetadataError("HTTP 404")
        assert (await cimd.resolve_client(_id(1))).client_id == _id(1)
        with pytest.raises(ClientMetadataError, match="404"):
            await cimd.resolve_client(_id(2))


class TestRealFetchDeadline(TestTemplate):
    """The deadline holds against servers that stall mid-response."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture(autouse=True)
    def _server(self):
        cimd.clear_cache()
        self.stall = StallServer()
        with (
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", _DEADLINE),
            patch.object(cimd, "validate_and_pin_async", side_effect=self._pin),
        ):
            yield
        self.stall.close()
        cimd.clear_cache()

    async def _pin(self, client_id: str, **_) -> PinnedTarget:
        # https://client.test.example/<path> -> the loopback server's <path>
        return PinnedTarget(url=self.stall.url("/" + client_id.split("/", 3)[3]))

    @pytest.mark.anyio
    @pytest.mark.parametrize("mode", ["flood", "header", "trailer", "body"])
    async def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        with pytest.raises(ClientMetadataError, match="too long"):
            await cimd.resolve_client(f"https://client.test.example/{mode}.json")
        assert time.monotonic() - start < _LIMIT
        _assert_clean()

    @pytest.mark.anyio
    async def test_a_served_document_resolves(self):
        client_id = "https://client.test.example/ok.json"
        self.stall.documents["/ok.json"] = client_document(client_id=client_id)
        assert (await cimd.resolve_client(client_id)).client_id == client_id
