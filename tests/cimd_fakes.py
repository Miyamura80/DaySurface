"""Fakes shared by the CIMD resolver tests.

``CimdCase`` gives each test a fresh ``cimd_cache.Resolver`` with small caps, a
fake network (``Net``) whose fetches finish exactly when the test says, a fake clock
for record lifetimes, and the app lifespan's task group around the test.
"""

from collections import Counter
from collections.abc import AsyncIterator
from unittest.mock import patch

import anyio
import pytest

from api_server.oauth import cimd, cimd_cache
from api_server.oauth.cimd_document import FetchedDocument
from services._ssrf_guard import PinnedTarget
from tests.oauth_server_fixture import client_document
from tests.test_template import TestTemplate

KNOWN = "https://known.test.example/meta.json"
USER = "user_01VOUCHER"
DEADLINE = 0.5
LIMIT = 1.5  # what a resolve may take: deadline, grace and slack


def cid(n: int | str) -> str:
    return f"https://c{n}.test.example/meta.json"


class Net:
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
        return FetchedDocument(answer, None)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def settled(client_id: str) -> None:
    """Wait for the fetch task of ``client_id``, if one is running."""
    flight = cimd._resolver.in_flight.get(client_id)
    if flight is not None:
        with anyio.fail_after(LIMIT):
            await flight.done.wait()


def assert_clean() -> None:
    state = cimd.snapshot()
    assert state.in_flight == ()
    assert state.fetches == 0
    assert state.known_fetches == 0


def small_resolver(**caps: int) -> cimd_cache.Resolver:
    defaults = {
        "fetches": 3,
        "resolver_threads": 3,
        "known_fetches": 2,
        "known_resolver_threads": 2,
    }
    return cimd_cache.Resolver(**(defaults | caps))


class CimdCase(TestTemplate):
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture(autouse=True)
    async def _fakes(self) -> AsyncIterator[None]:
        self.net = Net()
        self.clock = Clock()
        with (
            patch.object(cimd, "_resolver", small_resolver()),
            patch.object(cimd, "validate_and_pin_async", side_effect=self.net.pin),
            patch.object(cimd, "fetch_document", side_effect=self.net.fetch),
            patch.object(cimd_cache, "_clock", self.clock),
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", DEADLINE),
        ):
            async with cimd.lifespan():
                yield

    async def expect_error(self, client_id: str) -> None:
        with pytest.raises(cimd.ClientMetadataError):
            await cimd.resolve_client(client_id)
