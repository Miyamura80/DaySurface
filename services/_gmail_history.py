"""Paging ``users.history.list``: the mailbox change log since a historyId.

Gmail keeps history for about a week; asking for anything older is a 404,
raised here as ``HistoryGoneError`` so each caller picks its own fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from services._gmail_quota import HISTORY_LIST_UNITS, QuotaBudget

_PAGE_SIZE = 500


class HistoryGoneError(Exception):
    """Gmail no longer keeps history that far back (HTTP 404)."""


class HistoryTooLargeError(Exception):
    """The delta needs more pages than the caller allowed or can pay for."""


def iter_history_pages(
    svc: Any,
    start: str,
    *,
    history_types: list[str] | None = None,
    max_pages: int | None = None,
    budget: QuotaBudget | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each response page of the history after ``start``, oldest first.

    Raises ``HistoryGoneError`` on a 404, and ``HistoryTooLargeError``
    before reading a page past ``max_pages`` or one ``budget`` can't pay for.
    """
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    kwargs: dict[str, Any] = {
        "userId": "me",
        "startHistoryId": start,
        "maxResults": _PAGE_SIZE,
    }
    if history_types:
        kwargs["historyTypes"] = history_types
    page_token: str | None = None
    pages = 0
    while True:
        if max_pages is not None and pages >= max_pages:
            raise HistoryTooLargeError
        if budget is not None and not budget.charge(HISTORY_LIST_UNITS):
            raise HistoryTooLargeError
        pages += 1
        try:
            resp = svc.users().history().list(**kwargs, pageToken=page_token).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                raise HistoryGoneError from exc
            raise
        yield resp
        page_token = resp.get("nextPageToken")
        if not page_token:
            return
