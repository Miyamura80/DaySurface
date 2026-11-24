"""The CIMD resolver's state: cache tiers, known clients, budgets, lifetimes.

Kept apart from :mod:`api_server.oauth.cimd`, which owns the fetch flights and
the public API and documents the policy; nothing here awaits. All of it lives
on one :class:`Resolver`, so tests (and ``cimd.clear_cache``) start from a
fresh one.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

import anyio
from mcp.shared.auth import OAuthClientInformationFull

from services._ssrf_guard import ResolverThreads

DEFAULT_FRESH_SECONDS = 300
MIN_FRESH_SECONDS = 60  # a document cannot make every request a fetch
MAX_FRESH_SECONDS = 3600
MAX_STALE_SECONDS = 3600
FAILURE_CACHE_SECONDS = 60
BACKOFF_SECONDS = 30
MAX_KNOWN_PER_SUBJECT = 3
# Record lifetimes are measured on this clock; tests replace it.
_clock = time.monotonic


class Kind(Enum):
    DEFINITE = "definite"  # an answer about the document: cached, drops the record
    TRANSIENT = "transient"  # no answer: stale may be served, then back off
    BUSY = "busy"  # no capacity: stale may be served, then back off


@dataclass(frozen=True, slots=True)
class Fetched:
    client: OAuthClientInformationFull


@dataclass(frozen=True, slots=True)
class Failed:
    kind: Kind
    message: str


type Outcome = Fetched | Failed


@dataclass(frozen=True, slots=True)
class Record:
    client: OAuthClientInformationFull
    fresh_until: float
    stale_until: float  # equals fresh_until when the record may not go stale


@dataclass(frozen=True, slots=True)
class Until:
    """A failure message and when it stops applying."""

    message: str
    until: float


@dataclass(slots=True)
class Flight:
    """One fetch, which every resolve of its client_id waits on."""

    done: anyio.Event = field(default_factory=anyio.Event)
    outcome: Outcome | None = None  # None until done; stays None if cancelled


class BusyError(Exception):
    """No fetch slot is free in the budget."""


class Budget:
    """Fail-fast fetch slots plus the resolver threads their lookups may use.

    A plain counter under the resolver's lock, not an ``anyio.CapacityLimiter``:
    a limiter binds to the first event loop that uses it, and nothing here ever
    waits for a slot, which is all a limiter would add.
    """

    def __init__(self, fetches: int, resolver_threads: int) -> None:
        self.limit = fetches
        self.running = 0
        self.resolver = ResolverThreads(resolver_threads)

    def take(self) -> None:
        """Take a slot or raise :class:`BusyError`. Hold the resolver lock."""
        if self.running >= self.limit:
            raise BusyError
        self.running += 1


class LruDict[V](OrderedDict[str, V]):
    """An OrderedDict that drops its oldest entry beyond ``limit``."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def put(self, key: str, value: V) -> None:
        self.pop(key, None)
        while len(self) >= self.limit:
            self.popitem(last=False)
        self[key] = value


class Resolver:
    """All resolver state. One per process; tests build their own.

    Everything is guarded by ``lock``, which is never held across an await. A
    lock rather than loop confinement because ``mark_known`` is called from
    worker threads (storage runs off the loop).
    """

    def __init__(
        self,
        *,
        fetches: int = 64,
        resolver_threads: int = 32,
        known_fetches: int = 16,
        known_resolver_threads: int = 8,
        cache_entries: int = 1000,
        known_clients: int = 10_000,
    ) -> None:
        self.lock = threading.Lock()
        self.budget = Budget(fetches, resolver_threads)
        self.known_budget = Budget(known_fetches, known_resolver_threads)
        # client_id -> subjects vouching for it; oldest first.
        self.known: OrderedDict[str, set[str]] = OrderedDict()
        self.subjects: dict[str, OrderedDict[str, None]] = {}  # subject -> ids
        self.protected: dict[str, Record] = {}  # records of known clients
        self.cache: LruDict[Record] = LruDict(cache_entries)
        self.failures: LruDict[Until] = LruDict(cache_entries)
        self.backoff: LruDict[Until] = LruDict(cache_entries)
        self.known_backoff: dict[str, Until] = {}
        self.in_flight: dict[str, Flight] = {}
        self.known_limit = known_clients

    # -- known clients -------------------------------------------------------

    def mark_known(self, client_id: str, subject: str) -> None:
        with self.lock:
            ids = self.subjects.setdefault(subject, OrderedDict())
            ids[client_id] = None
            ids.move_to_end(client_id)
            while len(ids) > MAX_KNOWN_PER_SUBJECT:
                dropped = next(iter(ids))
                self._drop_subject_id(subject, dropped)
                self._unvouch(dropped, subject)
            vouchers = self.known.get(client_id)
            if vouchers is None:
                vouchers = set()
                if (record := self.cache.pop(client_id, None)) is not None:
                    self.protected[client_id] = record
                if (wait := self.backoff.pop(client_id, None)) is not None:
                    self.known_backoff[client_id] = wait
            vouchers.add(subject)
            self.known[client_id] = vouchers
            self.known.move_to_end(client_id)
            while len(self.known) > self.known_limit:
                evicted, gone = self.known.popitem(last=False)
                for who in gone:
                    self._drop_subject_id(who, evicted)
                self._demote(evicted)

    def _drop_subject_id(self, subject: str, client_id: str) -> None:
        ids = self.subjects.get(subject)
        if ids is not None:
            ids.pop(client_id, None)
            if not ids:
                del self.subjects[subject]

    def _unvouch(self, client_id: str, subject: str) -> None:
        vouchers = self.known.get(client_id)
        if vouchers is None:
            return
        vouchers.discard(subject)
        if not vouchers:
            del self.known[client_id]
            self._demote(client_id)

    def _demote(self, client_id: str) -> None:
        """No longer known: its record and backoff join the ordinary caches."""
        if (record := self.protected.pop(client_id, None)) is not None:
            self.cache.put(client_id, record)
        if (wait := self.known_backoff.pop(client_id, None)) is not None:
            self.backoff.put(client_id, wait)

    # -- lookups ---------------------------------------------------------------

    def record(self, client_id: str, now: float) -> Record | None:
        """The cached record, if still servable at all. Marks it used."""
        tier: dict[str, Record] = (
            self.protected if client_id in self.protected else self.cache
        )
        record = tier.get(client_id)
        if record is None:
            return None
        if record.stale_until <= now:
            del tier[client_id]  # past every use: free the memory now
            return None
        if tier is self.cache:
            self.cache.move_to_end(client_id)
        return record

    def backing_off(self, client_id: str, now: float) -> Until | None:
        tier = self.known_backoff if client_id in self.known else self.backoff
        wait = tier.get(client_id)
        if wait is not None and wait.until <= now:
            del tier[client_id]
            return None
        return wait

    def usable_stale(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.lock:
            record = self.record(client_id, now())
        return record.client if record is not None else None

    # -- results ---------------------------------------------------------------

    def store(
        self,
        client_id: str,
        client: OAuthClientInformationFull,
        fresh: float,
        stale: float,
    ) -> None:
        at = now()
        record = Record(client, at + fresh, at + fresh + stale)
        with self.lock:
            self.failures.pop(client_id, None)
            self.backoff.pop(client_id, None)
            self.known_backoff.pop(client_id, None)
            if client_id in self.known:
                self.protected[client_id] = record
            else:
                self.cache.put(client_id, record)

    def settle(self, client_id: str, outcome: Failed) -> None:
        """Apply a failed outcome. Hold lock."""
        until = now()
        if outcome.kind is Kind.DEFINITE:
            self.protected.pop(client_id, None)
            self.cache.pop(client_id, None)
            failure = Until(outcome.message, until + FAILURE_CACHE_SECONDS)
            self.failures.put(client_id, failure)
            return
        wait = Until(outcome.message, until + BACKOFF_SECONDS)
        if client_id in self.known:
            self.known_backoff[client_id] = wait
        else:
            self.backoff.put(client_id, wait)


def lifetimes(cache_control: str | None) -> tuple[float, float]:
    """Seconds fresh and seconds it may then be served stale."""
    directives: dict[str, str] = {}
    for part in (cache_control or "").split(","):
        name, _, value = part.partition("=")
        directives[name.strip().lower()] = value.strip().strip('"')
    if "no-store" in directives or "no-cache" in directives:
        # The RFC 9111 deviation in the module docstring: kept for the floor,
        # never served stale.
        return MIN_FRESH_SECONDS, 0.0
    fresh = DEFAULT_FRESH_SECONDS
    max_age = directives.get("max-age", "")
    if max_age.isdecimal():
        fresh = int(max_age[:6])  # six digits already exceed the clamp
    return min(max(fresh, MIN_FRESH_SECONDS), MAX_FRESH_SECONDS), MAX_STALE_SECONDS


# Record lifetimes are measured on this clock; tests replace it.
_clock = time.monotonic


def now() -> float:
    return _clock()
