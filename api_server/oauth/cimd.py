"""Resolve a Client ID Metadata Document (CIMD) client_id to a client record.

The URL shape, the fetch and the document itself are
:mod:`api_server.oauth.cimd_document`; this module decides when to fetch, how
many fetches may run, and what to serve while one cannot. Everything here runs
on the event loop: no worker thread ever waits on a fetch.

    resolve_client(id)
      fresh record ............................ return it
      cached definite failure ................. raise it
      known client, stale but usable .......... return it; refresh in background
      fetch already running for id ............ wait for it (a coroutine)
      otherwise take a fetch slot (fail fast) . run the fetch as its own task
                                                and wait for it

The fetch task, under one deadline (``_FETCH_DEADLINE_SECONDS``), resolves the
host on a capped resolver thread, checks every answer is public, pins the IP
and GETs the document from it. The deadline is a cancel scope, so it cuts off
a stalled DNS answer, connect, TLS handshake or body alike, and the slot is
released the moment it fires. The task outlives the request that started it,
so a client hanging up cannot abort a fetch other requests are waiting on.

Two budgets, each a fetch-slot cap plus a resolver-thread cap, refused at once
when full: one for known clients (``mark_known``: a client this server has
issued a code or token to) and one for everyone else. The cap is global, not
per target: a per-target key would be the attacker's own DNS answer. So a
flood of slow client ids can fill the shared budget and make new clients wait
(``busy``, never cached), but cannot touch known clients' budget or records.

Records honour the document's ``Cache-Control``: ``max-age`` sets freshness,
clamped to ``[_MIN_FRESH_SECONDS, _MAX_FRESH_SECONDS]`` with
``_DEFAULT_FRESH_SECONDS`` when absent. After that a record may be served stale
for at most ``_MAX_STALE_SECONDS``, and only when a refetch is busy or fails
without an answer (5xx, network error, timeout: RFC 5861 stale-if-error).
``no-store`` / ``no-cache`` records are never served stale. A definite failure
(4xx, an invalid or mismatched document, a refused host) drops the record and
is cached for ``_FAILURE_CACHE_SECONDS``; nothing else is failure-cached.

Records of known clients live in their own tier, which inserts from unknown
client ids cannot evict; unknown ids share an LRU of ``_MAX_CACHE_ENTRIES``.
"""

import asyncio
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum

import anyio
from mcp.shared.auth import OAuthClientInformationFull

from api_server.oauth.cimd_document import (
    ClientMetadataError,
    FetchedDocument,
    TransientFetchError,
    fetch_document,
    is_loopback_redirect,
    is_metadata_document_client_id,
    parse_document,
)
from services._ssrf_guard import (
    ResolverBusyError,
    ResolverThreads,
    SsrfError,
    UnresolvableHostError,
    validate_and_pin_async,
)

__all__ = [
    "ClientMetadataError",
    "is_loopback_redirect",
    "is_metadata_document_client_id",
    "mark_known",
    "parse_document",
    "resolve_client",
]

_FETCH_DEADLINE_SECONDS = 5.0
# A waiter gives the fetch task this long past the shared deadline to report,
# so the two timers firing together never turn a result into a timeout.
_WAIT_GRACE_SECONDS = 0.5
_DEFAULT_FRESH_SECONDS = 300
_MIN_FRESH_SECONDS = 60  # a document cannot make every request a fetch
_MAX_FRESH_SECONDS = 3600
_MAX_STALE_SECONDS = 3600
_FAILURE_CACHE_SECONDS = 60
_MAX_CACHE_ENTRIES = 1000
_MAX_KNOWN_CLIENTS = 10_000  # grows only by completed sign-ins
# Fetches are coroutines, so the caps are generous; they bound sockets and
# memory, not threads. Resolver threads are real threads, hence smaller.
_MAX_FETCHES = 64
_MAX_RESOLVER_THREADS = 32
_MAX_KNOWN_FETCHES = 16
_MAX_KNOWN_RESOLVER_THREADS = 8
# Record lifetimes are measured on this clock; tests replace it.
_clock = time.monotonic
_BUSY_MESSAGE = "too many client metadata fetches in progress"
_TIMEOUT_MESSAGE = "client metadata document took too long"


class _Kind(Enum):
    DEFINITE = "definite"  # an answer about the document: cached, drops the record
    TRANSIENT = "transient"  # no answer: a stale record may be served
    BUSY = "busy"  # no capacity: a stale record may be served, never cached


class _BusyError(Exception):
    """No fetch slot is free in the budget."""


@dataclass(frozen=True, slots=True)
class _Record:
    client: OAuthClientInformationFull
    fresh_until: float
    stale_until: float  # equals fresh_until when the record may not go stale


@dataclass(frozen=True, slots=True)
class _Failure:
    message: str
    until: float


@dataclass(slots=True)
class _Outcome:
    client: OAuthClientInformationFull | None = None
    kind: _Kind | None = None
    message: str = ""


@dataclass(slots=True)
class _Flight:
    """One fetch task, which every resolve of its client_id waits on."""

    done: anyio.Event = field(default_factory=anyio.Event)
    outcome: _Outcome | None = None  # None until done; stays None if cancelled


class _Budget:
    """Fail-fast fetch slots plus the resolver threads their lookups may use."""

    def __init__(self, fetches: int, resolver_threads: int) -> None:
        self.limit = fetches
        self.running = 0  # guarded by _lock
        self.resolver = ResolverThreads(resolver_threads)

    def take(self) -> None:
        """Take a slot. Hold _lock."""
        if self.running >= self.limit:
            raise _BusyError
        self.running += 1


@dataclass(frozen=True, slots=True)
class CimdSnapshot:
    """The resolver's state at one moment, for tests and diagnostics."""

    known: tuple[str, ...]
    protected: tuple[str, ...]
    cached: tuple[str, ...]
    failed: tuple[str, ...]
    in_flight: tuple[str, ...]
    fetches: int
    known_fetches: int
    resolver_threads: int


# All guarded by _lock, which is never held across an await. A lock rather
# than loop confinement because mark_known is called from worker threads.
_lock = threading.Lock()
_known: OrderedDict[str, None] = OrderedDict()  # oldest first
_protected: dict[str, _Record] = {}  # records of known clients
_cache: OrderedDict[str, _Record] = OrderedDict()  # everyone else, LRU
_failures: OrderedDict[str, _Failure] = OrderedDict()
_in_flight: dict[str, _Flight] = {}
_budget = _Budget(_MAX_FETCHES, _MAX_RESOLVER_THREADS)
_known_budget = _Budget(_MAX_KNOWN_FETCHES, _MAX_KNOWN_RESOLVER_THREADS)
# Strong references, so a running fetch task is not garbage collected.
_tasks: set[asyncio.Task[None]] = set()


def _bounded_insert[T](cache: OrderedDict[str, T], key: str, value: T) -> None:
    """Insert ``value`` as the newest entry, evicting the oldest. Hold _lock."""
    cache.pop(key, None)
    while len(cache) >= _MAX_CACHE_ENTRIES:
        cache.popitem(last=False)
    cache[key] = value


def mark_known(client_id: str) -> None:
    """Record that ``client_id`` holds a code or token from this server.

    Called by :mod:`api_server.oauth.storage` whenever it issues a code or
    tokens, or accepts an access token, so the set also refills after a
    restart as clients come back. A known client's record moves to the
    protected tier and its fetches use the reserved budget.
    """
    with _lock:
        _known[client_id] = None
        _known.move_to_end(client_id)
        while len(_known) > _MAX_KNOWN_CLIENTS:
            evicted, _ = _known.popitem(last=False)
            _protected.pop(evicted, None)
        if (record := _cache.pop(client_id, None)) is not None:
            _protected[client_id] = record


def _record(client_id: str) -> _Record | None:
    """The cached record, marked recently used. Hold _lock."""
    if (record := _protected.get(client_id)) is not None:
        return record
    if (record := _cache.get(client_id)) is not None:
        _cache.move_to_end(client_id)
    return record


def _lifetimes(cache_control: str | None) -> tuple[float, float]:
    """Seconds fresh and seconds it may then be served stale."""
    directives: dict[str, str] = {}
    for part in (cache_control or "").split(","):
        name, _, value = part.partition("=")
        directives[name.strip().lower()] = value.strip().strip('"')
    fresh = _DEFAULT_FRESH_SECONDS
    max_age = directives.get("max-age", "")
    if max_age.isdecimal():
        fresh = int(max_age[:6])  # six digits already exceed the clamp
    if "no-store" in directives or "no-cache" in directives:
        # Kept only for the floor, so it cannot make each request a fetch,
        # and never served stale.
        return _MIN_FRESH_SECONDS, 0.0
    return min(max(fresh, _MIN_FRESH_SECONDS), _MAX_FRESH_SECONDS), _MAX_STALE_SECONDS


def _usable_stale(client_id: str) -> OAuthClientInformationFull | None:
    with _lock:
        record = _record(client_id)
        if record is not None and record.stale_until > _clock():
            return record.client
    return None


async def resolve_client(client_id: str) -> OAuthClientInformationFull:
    """Return the validated client for ``client_id``, fetching it if needed.

    Raises :class:`ClientMetadataError` when the URL or its document is not
    acceptable, when it failed definitely within ``_FAILURE_CACHE_SECONDS``,
    or when no fetch can run or finish and there is no usable stale record.
    """
    if not is_metadata_document_client_id(client_id):
        raise ClientMetadataError("client_id is not a Client ID Metadata Document URL")
    deadline = _clock() + _FETCH_DEADLINE_SECONDS
    with _lock:
        now = _clock()
        record = _record(client_id)
        if record is not None and record.fresh_until > now:
            return record.client
        failure = _failures.get(client_id)
        if failure is not None and failure.until > now:
            raise ClientMetadataError(failure.message)
        known = client_id in _known
        stale_ok = record is not None and record.stale_until > now
        flight = _in_flight.get(client_id)
        if known and stale_ok and record is not None:
            # Stale-while-revalidate: a known client never waits on its own
            # host while its record is within the staleness bound.
            if flight is None:
                with suppress(_BusyError):
                    _start(client_id, _known_budget)
            return record.client
        if flight is None:
            try:
                flight = _start(client_id, _known_budget if known else _budget)
            except _BusyError:
                if stale_ok and record is not None:
                    return record.client
                raise ClientMetadataError(_BUSY_MESSAGE) from None
    return await _wait(client_id, flight, deadline)


def _start(client_id: str, budget: _Budget) -> _Flight:
    """Take a slot and start the fetch task. Hold _lock."""
    budget.take()
    flight = _in_flight[client_id] = _Flight()
    try:
        task = asyncio.get_running_loop().create_task(
            _fly(client_id, flight, budget), name=f"cimd-fetch {client_id}"
        )
    except BaseException:
        del _in_flight[client_id]
        budget.running -= 1
        raise
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return flight


async def _wait(
    client_id: str, flight: _Flight, deadline: float
) -> OAuthClientInformationFull:
    with anyio.move_on_after(deadline - _clock() + _WAIT_GRACE_SECONDS):
        await flight.done.wait()
    outcome = flight.outcome or _Outcome(kind=_Kind.TRANSIENT, message=_TIMEOUT_MESSAGE)
    if outcome.client is not None:
        return outcome.client
    if (
        outcome.kind is not _Kind.DEFINITE
        and (stale := _usable_stale(client_id)) is not None
    ):
        return stale
    raise ClientMetadataError(
        _BUSY_MESSAGE if outcome.kind is _Kind.BUSY else outcome.message
    )


async def _fly(client_id: str, flight: _Flight, budget: _Budget) -> None:
    """The fetch task: fetch, record the outcome, release everything."""
    outcome: _Outcome | None = None
    try:
        outcome = await _fetch(client_id, budget)
    finally:
        # Runs on success, failure and cancellation alike. The cache is
        # written before the flight goes, so a resolve arriving in between
        # finds one or the other.
        with _lock:
            budget.running -= 1
            if outcome is not None:
                _settle(client_id, outcome)
            del _in_flight[client_id]
        flight.outcome = outcome
        flight.done.set()


async def _fetch(client_id: str, budget: _Budget) -> _Outcome:
    try:
        with anyio.fail_after(_FETCH_DEADLINE_SECONDS):
            target = await validate_and_pin_async(client_id, resolver=budget.resolver)
            fetched = await fetch_document(target, timeout=_FETCH_DEADLINE_SECONDS)
        client = parse_document(client_id, fetched.body)
    except ResolverBusyError:
        return _Outcome(kind=_Kind.BUSY, message=_BUSY_MESSAGE)
    except TimeoutError:
        return _Outcome(kind=_Kind.TRANSIENT, message=_TIMEOUT_MESSAGE)
    except UnresolvableHostError as exc:
        return _Outcome(kind=_Kind.TRANSIENT, message=f"client_id host: {exc}")
    except SsrfError as exc:
        return _Outcome(
            kind=_Kind.DEFINITE, message=f"client_id host is not allowed: {exc}"
        )
    except TransientFetchError as exc:
        return _Outcome(kind=_Kind.TRANSIENT, message=str(exc))
    except ClientMetadataError as exc:
        return _Outcome(kind=_Kind.DEFINITE, message=str(exc))
    return _store(client_id, client, fetched)


def _store(
    client_id: str, client: OAuthClientInformationFull, fetched: FetchedDocument
) -> _Outcome:
    fresh, stale = _lifetimes(fetched.cache_control)
    now = _clock()
    record = _Record(client, now + fresh, now + fresh + stale)
    with _lock:
        _failures.pop(client_id, None)
        if client_id in _known:
            _protected[client_id] = record
        else:
            _bounded_insert(_cache, client_id, record)
    return _Outcome(client=client)


def _settle(client_id: str, outcome: _Outcome) -> None:
    """Apply a failed outcome to the cache. Hold _lock."""
    if outcome.kind is _Kind.DEFINITE:
        _protected.pop(client_id, None)
        _cache.pop(client_id, None)
        until = _clock() + _FAILURE_CACHE_SECONDS
        _bounded_insert(_failures, client_id, _Failure(outcome.message, until))


def snapshot() -> CimdSnapshot:
    with _lock:
        return CimdSnapshot(
            known=tuple(_known),
            protected=tuple(_protected),
            cached=tuple(_cache),
            failed=tuple(_failures),
            in_flight=tuple(_in_flight),
            fetches=_budget.running,
            known_fetches=_known_budget.running,
            resolver_threads=_budget.resolver.running + _known_budget.resolver.running,
        )


def clear_cache() -> None:
    """Forget every record, failure and known client (tests)."""
    with _lock:
        _known.clear()
        _protected.clear()
        _cache.clear()
        _failures.clear()
