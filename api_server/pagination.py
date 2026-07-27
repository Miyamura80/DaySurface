"""Cursor-pagination helpers for REST routes (keyset, not offset/page).

A cursor is an opaque, URL-safe base64 token wrapping the sort key of the last
row on a page - here ``(created_at, id)``, which is the stable composite key
every list endpoint orders by. Encoding the *position* (rather than an offset)
keeps pagination correct under concurrent writes and avoids ``OFFSET`` scans.

The token is opaque on purpose: clients must treat it as a blob and pass it
back verbatim, so the server can change its internal shape without breaking
them.

Usage in a route::

    page: CursorParams = Depends()
    if page.cursor:
        created_at, last_id = decode_cursor(page.cursor)
        query = query.filter(keyset_before(Model, created_at, last_id))
    rows = query.order_by(Model.created_at.desc(), Model.id.desc()) \
                .limit(page.limit + 1).all()
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query
from sqlalchemy import and_, or_

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# URL-safe base64 alphabet (no padding - we strip and re-add it ourselves).
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class CursorParams:
    """Reusable ``limit`` + ``cursor`` query parameters for list endpoints."""

    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Maximum items to return (1-{MAX_LIMIT}).",
    )
    cursor: str | None = Query(
        None,
        description="Opaque pagination cursor returned as `next_cursor` by a prior call.",
    )


def encode_cursor(created_at: datetime, item_id: int) -> str:
    """Encode a keyset position into an opaque, URL-safe cursor token."""
    raw = json.dumps({"c": created_at.isoformat(), "i": item_id}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Decode a cursor token back into ``(created_at, id)``.

    Raises ``HTTPException(400)`` on any malformed token so a bad client cursor
    surfaces as a clean validation error rather than a 500.
    """
    # Reject anything outside the URL-safe base64 alphabet up front;
    # urlsafe_b64decode silently discards stray bytes rather than failing, so a
    # malformed cursor would otherwise slip through to a confusing decode error.
    if not _CURSOR_RE.match(cursor):
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(data["c"]), int(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid pagination cursor"
        ) from exc


def keyset_before(model: Any, created_at: datetime, item_id: int):
    """Build a ``WHERE`` clause selecting rows *after* a cursor in DESC order.

    ``model`` is a SQLAlchemy declarative class exposing ``created_at`` and
    ``id`` columns. Expressed as ``created_at < c OR (created_at = c AND id <
    id)`` rather than a row-value tuple comparison so it is portable across
    SQLite and Postgres.
    """
    return or_(
        model.created_at < created_at,
        and_(model.created_at == created_at, model.id < item_id),
    )
