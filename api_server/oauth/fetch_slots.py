"""Concurrency limits for Client ID Metadata Document fetches.

Each fetch targets a host the client picked, so a slow host must not be able to
hold every fetch slot and lock other clients out of signing in. Fetches are
limited in two ways:

* per host: a host gets at most ``per_host`` running fetches and ``per_host``
  queued ones, so one slow host (or a handful) cannot take every slot;
* in total: at most ``total`` fetches run and ``max_waiting`` wait at once, so
  a flood of slow hosts cannot occupy the worker threads that also serve /mcp.

A caller that finds no free slot waits a short, bounded time for one rather
than failing at once, which absorbs a burst of sign-ins for the same client.

The per-host counts live in a dict whose entries are removed as soon as a host
has nothing running or queued, so it never holds more than
``total + max_waiting`` entries.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class FetchSlotsBusyError(RuntimeError):
    """No fetch slot became free in time."""


class FetchSlots:
    """Per-host and global fetch limits with a short bounded queue."""

    def __init__(self, *, total: int, per_host: int, max_waiting: int) -> None:
        self._total = total
        self._per_host = per_host
        self._max_waiting = max_waiting
        self._cond = threading.Condition()
        self._running = 0
        self._waiting = 0
        # host -> [running, waiting]; an entry exists only while both aren't 0.
        self._hosts: dict[str, list[int]] = {}

    def _free_for(self, counts: list[int]) -> bool:
        return self._running < self._total and counts[0] < self._per_host

    @contextmanager
    def hold(self, host: str, timeout: float) -> Iterator[None]:
        """Hold a slot for ``host`` while the block runs.

        Waits at most ``timeout`` seconds for one, and raises
        :class:`FetchSlotsBusyError` if none frees up or the queue is full.
        """
        with self._cond:
            counts = self._hosts.setdefault(host, [0, 0])
            try:
                if not self._free_for(counts):
                    if (
                        timeout <= 0
                        or self._waiting >= self._max_waiting
                        or counts[1] >= self._per_host
                    ):
                        raise FetchSlotsBusyError
                    self._waiting += 1
                    counts[1] += 1
                    try:
                        ready = self._cond.wait_for(
                            lambda: self._free_for(counts), timeout
                        )
                    finally:
                        self._waiting -= 1
                        counts[1] -= 1
                    if not ready:
                        raise FetchSlotsBusyError
            except FetchSlotsBusyError:
                self._forget_if_idle(host, counts)
                raise
            self._running += 1
            counts[0] += 1
        try:
            yield
        finally:
            with self._cond:
                self._running -= 1
                counts[0] -= 1
                self._forget_if_idle(host, counts)
                self._cond.notify_all()

    def _forget_if_idle(self, host: str, counts: list[int]) -> None:
        """Drop ``host``'s entry once nothing runs or waits for it. Hold _cond."""
        if counts == [0, 0]:
            del self._hosts[host]

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """``host -> (running, waiting)``, for tests and diagnostics."""
        with self._cond:
            return {host: (c[0], c[1]) for host, c in self._hosts.items()}
