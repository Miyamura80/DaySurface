"""Resolve a Client ID Metadata Document (CIMD) client_id to a client record.

The URL shape, the fetch and the document itself are
:mod:`api_server.oauth.cimd_document`, and the cache state and its constants
(the names below without a module) :mod:`api_server.oauth.cimd_cache`; this
module decides when to fetch, how
many fetches may run, and what to serve while one cannot. Everything here runs
on the event loop: no worker thread ever waits on a fetch.

    resolve_client(id)
      fresh record ............................ return it
      cached definite failure ................. raise it
      backing off after a failed refetch ...... stale record, else raise
      known client, stale but usable .......... return it; refresh in background
      fetch already running for id ............ wait for it (a coroutine)
      otherwise take a fetch slot (fail fast) . run the fetch as a task in the
                                                lifespan's task group, wait on it

The fetch task, under one deadline (``_FETCH_DEADLINE_SECONDS``), resolves the
host on a capped resolver thread, checks every answer is public, pins an IP
and GETs the document from it. The deadline is a cancel scope, so it cuts off
a stalled DNS answer, connect, TLS handshake or body alike, and the slot is
released the moment it fires. A host whose previous lookup is still running
(the system resolver has no timeout we control) gets no second thread. The
task outlives the request that started it, so a client hanging up cannot
abort a fetch other requests are waiting on. Outside the app lifespan (tests,
scripts) there is no task group and the first resolve runs the fetch itself.

Two budgets, each a fetch-slot cap plus a resolver-thread cap, refused at once
when full: one for known clients and one for everyone else. The cap is global,
not per target: a per-target key would be the attacker's own DNS answer. So a
flood of slow client ids can fill the shared budget and make new clients wait
(``busy``, never cached), but cannot touch known clients' budget or records.

A client is known once this server issued it a code or tokens, or accepted
its access token (``mark_known``, called by storage and the provider, after
the commit, with the user it was for).
Each user can vouch for at most ``MAX_KNOWN_PER_SUBJECT`` client ids, so
signing in with one's own client ids buys a bounded share of the reserved
budget, and known ids are capped overall (``Resolver(known_clients=)``); an id that
loses its last voucher becomes an ordinary cached entry again.

Records honour the document's ``Cache-Control``: ``max-age`` sets freshness,
clamped to ``[MIN_FRESH_SECONDS, MAX_FRESH_SECONDS]`` with
``DEFAULT_FRESH_SECONDS`` when absent. After freshness a record may be served
stale for at most ``MAX_STALE_SECONDS``: to a known client always, while one
background refetch runs (stale-while-revalidate); to anyone while a refetch is
busy, backing off, or fails without an answer (5xx, network error, timeout:
RFC 5861 stale-if-error). ``no-store`` / ``no-cache`` records are never served
stale. A definite failure (4xx, an invalid or mismatched document, a refused
host) drops the record and is cached for ``FAILURE_CACHE_SECONDS``. Any other
failed fetch starts a per-id backoff of ``BACKOFF_SECONDS``, during which the
id is not fetched again, so one id costs at most one fetch per backoff.

Deliberate deviation from RFC 9111: a ``no-store`` / ``no-cache`` document is
still kept for ``MIN_FRESH_SECONDS``, without revalidation. Taken literally,
those directives would make every authorize, consent and token request a
fetch of a URL the client chooses; the floor bounds that, and stale serving
stays forbidden.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import anyio
from anyio.abc import TaskGroup
from loguru import logger as log
from mcp.shared.auth import OAuthClientInformationFull

from api_server.oauth.cimd_cache import (
    Budget,
    BusyError,
    Failed,
    Fetched,
    Flight,
    Kind,
    Outcome,
    Record,
    Resolver,
    lifetimes,
    now,
)
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
    SsrfError,
    UnresolvableHostError,
    validate_and_pin_async,
)

__all__ = [
    "ClientMetadataError",
    "is_loopback_redirect",
    "is_metadata_document_client_id",
    "lifespan",
    "mark_known",
    "parse_document",
    "resolve_client",
]

_FETCH_DEADLINE_SECONDS = 5.0
# A waiter gives the fetch task this long past the shared deadline to report,
# so the two timers firing together never turn a result into a timeout.
_WAIT_GRACE_SECONDS = 0.5
_BUSY_MESSAGE = "too many client metadata fetches in progress"
_TIMEOUT_MESSAGE = "client metadata document took too long"
_TIMED_OUT = Failed(Kind.TRANSIENT, _TIMEOUT_MESSAGE)


@dataclass(frozen=True, slots=True)
class CimdSnapshot:
    """The resolver's state at one moment, for tests and diagnostics."""

    known: tuple[str, ...]
    protected: tuple[str, ...]
    cached: tuple[str, ...]
    failed: tuple[str, ...]
    backing_off: tuple[str, ...]
    in_flight: tuple[str, ...]
    fetches: int
    known_fetches: int
    resolver_threads: int


_resolver = Resolver()
# The app lifespan's task group, which owns every fetch task (see lifespan).
_task_group: TaskGroup | None = None


def mark_known(client_id: str, subject: str) -> None:
    """Record that ``subject`` (a user) holds a code or token for ``client_id``.

    Called by :mod:`api_server.oauth.storage` and the provider once a code or
    tokens are committed, and whenever an access token is accepted, so the set
    refills after a restart as clients come back. A known client's record moves to the protected tier
    and its fetches use the reserved budget.
    """
    _resolver.mark_known(client_id, subject)


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    """Own the fetch tasks for the app's lifetime; cancel them at shutdown."""
    global _task_group  # noqa: PLW0603
    async with anyio.create_task_group() as tg:
        _task_group = tg
        try:
            yield
        finally:
            _task_group = None
            tg.cancel_scope.cancel()


async def resolve_client(client_id: str) -> OAuthClientInformationFull:
    """Return the validated client for ``client_id``, fetching it if needed.

    Raises :class:`ClientMetadataError` when the URL or its document is not
    acceptable, when it failed definitely within ``FAILURE_CACHE_SECONDS``,
    or when no fetch can run or finish and there is no usable stale record.
    """
    if not is_metadata_document_client_id(client_id):
        raise ClientMetadataError("client_id is not a Client ID Metadata Document URL")
    resolver = _resolver
    deadline = now() + _FETCH_DEADLINE_SECONDS
    with resolver.lock:
        found = _from_cache(resolver, client_id, now())
        if isinstance(found, OAuthClientInformationFull):
            return found
        record = found
        known = client_id in resolver.known
        budget = resolver.known_budget if known else resolver.budget
        flight = resolver.in_flight.get(client_id)
        lead = None
        if known and record is not None and _task_group is not None:
            # Stale-while-revalidate: a known client never waits on its own
            # host while its record is within the staleness bound.
            if flight is None:
                with suppress(BusyError):
                    _start(resolver, client_id, budget)
            return record.client
        if flight is None:
            try:
                flight = lead = _start(resolver, client_id, budget)
            except BusyError:
                if record is not None:
                    return record.client
                raise ClientMetadataError(_BUSY_MESSAGE) from None
    if lead is not None and _task_group is None:
        await _fly(resolver, client_id, lead, budget)  # no task group: run it here
    return await _wait(resolver, client_id, flight, deadline)


def _from_cache(
    resolver: Resolver, client_id: str, now: float
) -> OAuthClientInformationFull | Record | None:
    """A fresh client, else the stale record (or None) to fall back on.

    Raises a cached definite failure, or the last failure while backing off
    with no stale record. Hold the lock.
    """
    record = resolver.record(client_id, now)
    if record is not None and record.fresh_until > now:
        return record.client
    failure = resolver.failures.get(client_id)
    if failure is not None and failure.until > now:
        raise ClientMetadataError(failure.message)
    if (wait := resolver.backing_off(client_id, now)) is not None:
        if record is not None:
            return record.client
        raise ClientMetadataError(wait.message)
    return record


def _start(resolver: Resolver, client_id: str, budget: Budget) -> Flight:
    """Take a slot and start the fetch, if a task group is running. Hold lock."""
    budget.take()
    flight = resolver.in_flight[client_id] = Flight()
    if _task_group is not None:
        try:
            _task_group.start_soon(
                _fly, resolver, client_id, flight, budget, name=f"cimd {client_id}"
            )
        except RuntimeError:  # the task group is shutting down
            del resolver.in_flight[client_id]
            budget.running -= 1
            raise BusyError from None
    return flight


async def _wait(
    resolver: Resolver, client_id: str, flight: Flight, deadline: float
) -> OAuthClientInformationFull:
    with anyio.move_on_after(deadline - now() + _WAIT_GRACE_SECONDS):
        await flight.done.wait()
    outcome = flight.outcome or _TIMED_OUT
    if isinstance(outcome, Fetched):
        return outcome.client
    stale = None if outcome.kind is Kind.DEFINITE else resolver.usable_stale(client_id)
    if stale is not None:
        return stale
    raise ClientMetadataError(
        _BUSY_MESSAGE if outcome.kind is Kind.BUSY else outcome.message
    )


async def _fly(
    resolver: Resolver, client_id: str, flight: Flight, budget: Budget
) -> None:
    """The fetch task: fetch, record the outcome, release everything."""
    outcome: Outcome | None = None
    try:
        outcome = await _fetch(resolver, client_id, budget)
    except Exception:  # noqa: BLE001
        # Task boundary: an unexpected error must not escape into the
        # lifespan's task group (it would cancel every fetch and the app).
        log.exception("oauth: client metadata fetch crashed for {}", client_id)
        outcome = _TIMED_OUT
    finally:
        # Runs on success, failure and cancellation alike. The cache is
        # written before the flight goes, so a resolve arriving in between
        # finds one or the other.
        with resolver.lock:
            if isinstance(outcome, Failed):
                resolver.settle(client_id, outcome)
            del resolver.in_flight[client_id]
            budget.running -= 1
        flight.outcome = outcome
        flight.done.set()


async def _fetch(resolver: Resolver, client_id: str, budget: Budget) -> Outcome:
    try:
        with anyio.fail_after(_FETCH_DEADLINE_SECONDS):
            target = await validate_and_pin_async(client_id, resolver=budget.resolver)
            fetched = await fetch_document(target, timeout=_FETCH_DEADLINE_SECONDS)
        client = parse_document(client_id, fetched.body)
    except ResolverBusyError:
        return Failed(Kind.BUSY, _BUSY_MESSAGE)
    except TimeoutError:
        return _TIMED_OUT
    except UnresolvableHostError as exc:
        return Failed(Kind.TRANSIENT, f"client_id host: {exc}")
    except SsrfError as exc:
        return Failed(Kind.DEFINITE, f"client_id host is not allowed: {exc}")
    except TransientFetchError as exc:
        return Failed(Kind.TRANSIENT, str(exc))
    except ClientMetadataError as exc:
        return Failed(Kind.DEFINITE, str(exc))
    _store(resolver, client_id, client, fetched)
    return Fetched(client)


def _store(
    resolver: Resolver,
    client_id: str,
    client: OAuthClientInformationFull,
    fetched: FetchedDocument,
) -> None:
    fresh, stale = lifetimes(fetched.cache_control)
    resolver.store(client_id, client, fresh, stale)


def snapshot() -> CimdSnapshot:
    resolver = _resolver
    with resolver.lock:
        return CimdSnapshot(
            known=tuple(resolver.known),
            protected=tuple(resolver.protected),
            cached=tuple(resolver.cache),
            failed=tuple(resolver.failures),
            backing_off=(*resolver.backoff, *resolver.known_backoff),
            in_flight=tuple(resolver.in_flight),
            fetches=resolver.budget.running,
            known_fetches=resolver.known_budget.running,
            resolver_threads=resolver.budget.resolver.running
            + resolver.known_budget.resolver.running,
        )


def clear_cache() -> None:
    """Start over with a fresh resolver: records, flights, budgets (tests)."""
    global _resolver  # noqa: PLW0603
    _resolver = Resolver()
