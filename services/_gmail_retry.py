"""Rate-limit backoff for every Gmail API request.

Gmail enforces a per-user "query cost per minute" quota, and a bursty caller
(the daily brief fanning out reads) trips it. Without a retry the raw
``HttpError`` reaches the host verbatim, project number and all, so the tool
just fails.

``RateLimitRetryingRequest`` is passed to ``googleapiclient.discovery.build``
as its ``requestBuilder``, so every ``.execute()`` on the client goes through
it without touching call sites. It retries only rate-limit rejections (429,
and 403 with a rate-limit reason). Those are refused before Gmail does any
work, so retrying is safe even for ``messages.send``. It deliberately does not
retry 5xx or connection errors the way ``execute(num_retries=...)`` would: a
5xx on a send may already have delivered the mail.

Batch requests are untouched: per-thread failures inside a batch go to the
batch callback, and the curation code already reads a missing thread as
stale rather than fresh.
"""

from __future__ import annotations

import json
import math
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest
from loguru import logger as log

from services import RetryLaterError

_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
# Delays double from the base: 1 + 2 + 4 + 8 s, about 15 s before giving up,
# enough for a per-minute window to start draining. A Retry-After from Gmail
# replaces a step, so with the cap the worst case is about 4 x 25 s.
_MAX_RETRIES = 4
_BASE_DELAY_S = 1.0
_MAX_RETRY_AFTER_S = 20.0


class GmailRateLimitedError(RetryLaterError):
    """Gmail kept rate-limiting after the retries ran out.

    Over MCP the message is the ``isError`` tool-result text, so it says what
    to do next and carries none of the raw Google error (which names the
    server's Google Cloud project).
    """

    def __init__(self) -> None:
        super().__init__(
            "Gmail's API rate limit for this account was hit and did not clear "
            "after retrying. Wait about a minute, then retry this tool. Avoid "
            "issuing many Gmail tool calls at once."
        )


def _error_reasons(content: bytes | None) -> set[str]:
    """Collect every ``reason`` string in a Google API error body."""
    if not content:
        return set()
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return set()
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons: set[str] = set()
    for key in ("errors", "details"):
        for item in error.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                reasons.add(item["reason"])
    return reasons


def is_rate_limited(exc: HttpError) -> bool:
    status = exc.resp.status
    if status == 429:
        return True
    return status == 403 and bool(_error_reasons(exc.content) & _RATE_LIMIT_REASONS)


def _retry_after_s(value: str | None) -> float | None:
    """Seconds a ``Retry-After`` header asks for (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)


def _retry_delay(exc: HttpError, attempt: int) -> float:
    retry_after = _retry_after_s(exc.resp.get("retry-after"))
    # Jittered so concurrent calls for one user don't retry in lockstep. A
    # Retry-After only ever gets stretched, never retried earlier than asked.
    if retry_after is not None:
        return retry_after * random.uniform(1.0, 1.25)  # noqa: S311 - jitter, not crypto
    return _BASE_DELAY_S * (2**attempt) * random.uniform(0.75, 1.25)  # noqa: S311 - jitter, not crypto


class RateLimitRetryingRequest(HttpRequest):
    """``HttpRequest`` that backs off and retries Gmail rate-limit rejections."""

    def execute(self, http: Any = None, num_retries: int = 0) -> Any:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return super().execute(http=http, num_retries=num_retries)
            except HttpError as exc:
                if not is_rate_limited(exc):
                    raise
                if attempt == _MAX_RETRIES:
                    raise GmailRateLimitedError() from exc
                delay = _retry_delay(exc, attempt)
                log.warning(
                    "Gmail rate limit on {} {}; retry {}/{} in {:.1f}s",
                    self.method,
                    self.methodId,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
