"""Cursor-pagination contracts shared by every transport.

Agent-friendly collections are cursor-based, not offset/page-based: an opaque
cursor encodes a keyset position, so results stay stable while rows are inserted
or deleted between page fetches (no skipped/duplicated items, no expensive
``OFFSET`` scans). The envelope is deliberately minimal - ``items`` plus a
forward ``next_cursor`` - because that is all an autonomous client needs to
loop until ``has_more`` is false.
"""

from pydantic import BaseModel, Field


class CursorPage[T](BaseModel):
    """A single page of a cursor-paginated collection.

    Iterate by passing ``next_cursor`` back as the ``cursor`` query parameter
    until ``has_more`` is ``False`` (at which point ``next_cursor`` is ``None``).
    """

    items: list[T] = Field(description="The items on this page.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque cursor for the next page. Pass it back as `cursor`. "
            "`null` when there are no more items."
        ),
    )
    has_more: bool = Field(
        default=False,
        description="True when another page is available via `next_cursor`.",
    )
