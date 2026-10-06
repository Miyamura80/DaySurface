"""Gmail quota units one tool call may spend.

Gmail meters each user by quota units per minute. New Cloud projects since
May 2026 get 6,000, and ``threads.get`` costs 40 of them, so a read that
fetches one thread per candidate can spend the whole minute on its own and
leave every following request refused. A ``QuotaBudget`` is one pool a call
draws every optional request from, so it degrades (fewer checks) instead of
starving the user's next call.

Prices are the new-tier ones. Older projects pay less (``threads.get`` is
10), so a budget sized on these prices only ever errs toward spending less.
"""

from __future__ import annotations

from loguru import logger as log

THREADS_GET_UNITS = 40
HISTORY_LIST_UNITS = 2
# A third of the strictest per-user minute: room for this call plus one more
# read (e.g. inbox_search after inbox_get_curation). Back-to-back calls still
# share the minute, so this caps one call, not a burst.
DEFAULT_CALL_UNITS = 2_000


class QuotaBudget:
    """Quota units one tool call may still spend."""

    def __init__(self, units: int | None = None) -> None:
        self.remaining = DEFAULT_CALL_UNITS if units is None else units
        self._short = False

    def charge(self, units: int) -> bool:
        """Spend ``units`` if they are left; False (and nothing spent) if not."""
        if units > self.remaining:
            self._note_short()
            return False
        self.remaining -= units
        return True

    def take[T](self, items: list[T], unit_cost: int) -> list[T]:
        """The leading ``items`` the budget covers at ``unit_cost`` each, charged."""
        allowed = items[: self.remaining // unit_cost]
        self.remaining -= len(allowed) * unit_cost
        if len(allowed) < len(items):
            self._note_short()
        return allowed

    def _note_short(self) -> None:
        if not self._short:
            self._short = True
            log.info("Gmail quota budget for this call is spent; skipping checks")
