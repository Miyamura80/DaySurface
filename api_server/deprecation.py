"""Endpoint deprecation & sunset signalling (RFC 9745 + RFC 8594).

FastAPI's ``deprecated=True`` flag only marks an operation in the OpenAPI
schema. Agents and HTTP clients discover lifecycle at *runtime* via response
headers, so a deprecated route should emit both:

- ``Deprecation`` (RFC 9745) - an sf-date (``@<unix-timestamp>``) for when the
  endpoint became deprecated, or the bare token ``true`` if no date is given.
- ``Sunset`` (RFC 8594) - an IMF-fixdate HTTP-date for when it stops working.
- ``Link; rel="deprecation"`` - a pointer to the human-readable policy page so
  a client can surface migration guidance.

Attach :func:`deprecate` as a route dependency *and* pass ``deprecated=True`` to
the decorator so the runtime headers and the schema agree::

    @router.get(
        "/old",
        dependencies=[Depends(deprecate(sunset=datetime(2026, 12, 31, tzinfo=UTC)))],
        deprecated=True,
    )
    def old_endpoint(): ...

See ``docs/content/docs/api/deprecation.mdx`` for the published policy.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime

from fastapi import Response

# Public deprecation-policy page advertised in the ``Link`` header. Kept
# relative so it resolves against whichever host serves the docs; override per
# deployment if the policy lives elsewhere.
DEPRECATION_POLICY_URL = "https://gmailmcp.com/docs/api/deprecation"


def _to_utc(value: datetime) -> datetime:
    """Normalize to UTC, treating a naive datetime as already-UTC (not local)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _http_date(value: datetime) -> str:
    """Format a datetime as an IMF-fixdate (RFC 7231), e.g. for ``Sunset``."""
    return format_datetime(_to_utc(value), usegmt=True)


def deprecate(
    *,
    since: datetime | None = None,
    sunset: datetime | None = None,
    policy_url: str = DEPRECATION_POLICY_URL,
) -> Callable[[Response], None]:
    """Build a route dependency that stamps deprecation headers on the response.

    Args:
        since: When the endpoint became deprecated. Emitted as the RFC 9745
            ``Deprecation`` sf-date. RFC 9745 has no bare "deprecated" token, so
            when omitted we default to the time the route is declared (captured
            once here, stable across requests) rather than emit a non-conformant
            value.
        sunset: When the endpoint will stop working. Emitted as the RFC 8594
            ``Sunset`` HTTP-date; omitted entirely when ``None``.
        policy_url: Target of the ``Link; rel="deprecation"`` header.
    """
    effective_since = since if since is not None else datetime.now(UTC)

    def _dependency(response: Response) -> None:
        ts = int(_to_utc(effective_since).timestamp())
        response.headers["Deprecation"] = f"@{ts}"
        if sunset is not None:
            response.headers["Sunset"] = _http_date(sunset)
        response.headers["Link"] = f'<{policy_url}>; rel="deprecation"'

    return _dependency
