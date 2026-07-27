"""Pydantic contracts for the inbox curation ledger.

Shared input/output schemas so CLI, MCP, and HTTP agree on the curation shape.
``CurationRecord`` is the decrypted, host-facing view of a ``thread_curation``
row; the ``*_enc`` bytes never leave the service layer.

Curation is produced only by the host LLM and banked through
``inbox_save_curation``; the ledger read (``inbox_get_curation``) and the
headless deep search (``inbox_search``) never run inference.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CurationBucket(StrEnum):
    """Where the host LLM sorted a thread."""

    needs_reply = "needs_reply"
    waiting_on = "waiting_on"
    fyi = "fyi"
    noise = "noise"


class SuggestedAction(StrEnum):
    """The next action the host LLM proposes for a thread."""

    archive = "archive"
    reply = "reply"
    mark_done = "mark_done"
    none = "none"


class CurationState(StrEnum):
    """Lifecycle of a ledger row.

    - ``pending``   - a row exists but has not been curated (reserved for a
      future server-side prior; the host never writes this).
    - ``curated``   - the host LLM has recorded a judgment.
    - ``acted``     - an action tool (archive / mark-done / reply) has run.
    - ``dismissed`` - the thread was archived / marked done (removed from inbox).
    """

    pending = "pending"
    curated = "curated"
    acted = "acted"
    dismissed = "dismissed"


class LedgerStatus(StrEnum):
    """Per-thread freshness annotation computed on read (not stored).

    - ``curated``   - a fresh ledger row exists for the thread.
    - ``stale``     - a ledger row exists but the thread's Gmail historyId has
      advanced past the curated watermark (needs re-reasoning).
    - ``uncurated`` - no ledger row (or a ``pending`` one); never judged.
    """

    curated = "curated"
    stale = "stale"
    uncurated = "uncurated"


class CurationRecord(BaseModel):
    """Decrypted, host-facing curation verdict for one thread."""

    thread_id: str
    bucket: CurationBucket | None = None
    importance: float | None = None
    summary: str | None = None
    reasoning: str | None = None
    suggested_action: SuggestedAction = SuggestedAction.none
    draft_id: str | None = None
    confidence: float | None = None
    state: CurationState = CurationState.curated
    curator_version: str | None = None
    curated_history_id: str | None = None
    curated_at: datetime | None = None
    updated_at: datetime | None = None
    # Computed on read, not persisted:
    ledger_status: LedgerStatus = LedgerStatus.curated


class CoverageSummary(BaseModel):
    """How much of the current inbox is covered by the ledger.

    The host reads these counts to decide whether to go deeper: a large
    ``uncurated`` or ``stale`` count is the signal to run a deep pass.
    """

    curated: int = 0
    stale: int = 0
    uncurated: int = 0


# ---------------------------------------------------------------------------
# inbox_get_curation
# ---------------------------------------------------------------------------


class GetCurationInput(BaseModel):
    user_id: str = ""
    bucket: CurationBucket | None = None
    state: CurationState | None = None
    fresh_only: bool = Field(
        default=False,
        description="Drop rows whose thread has changed since it was curated.",
    )
    check_freshness: bool = Field(
        default=True,
        description=(
            "Compare each row's curated historyId against the thread's current "
            "Gmail historyId to flag stale rows. Uses one ids-only threads.list "
            "(no message bodies, no inference)."
        ),
    )
    limit: int = Field(default=50, ge=1, le=500)


class GetCurationResult(BaseModel):
    records: list[CurationRecord]
    coverage: CoverageSummary


# ---------------------------------------------------------------------------
# inbox_save_curation
# ---------------------------------------------------------------------------


class ThreadJudgment(BaseModel):
    """One host-produced judgment to bank."""

    thread_id: str = Field(min_length=1)
    bucket: CurationBucket
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str | None = None
    reasoning: str | None = None
    suggested_action: SuggestedAction = SuggestedAction.none
    draft_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SaveCurationInput(BaseModel):
    user_id: str = ""
    judgments: list[ThreadJudgment] = Field(default_factory=list)
    curator_version: str | None = Field(
        default=None,
        description="Model/prompt version tag stamped on each written row.",
    )


class SaveCurationResult(BaseModel):
    saved: int
    thread_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# inbox_search
# ---------------------------------------------------------------------------


class InboxSearchItem(BaseModel):
    thread_id: str
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    last_message_at: datetime | None = None
    ledger_status: LedgerStatus = LedgerStatus.uncurated
    importance_prior: float | None = Field(
        default=None,
        description="Heuristic prior importance for an uncurated thread (0..~2).",
    )

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class InboxSearchInput(BaseModel):
    user_id: str = ""
    query: str | None = None
    limit: int = Field(default=25, ge=1, le=200)
    since_history_id: str | None = Field(
        default=None,
        description=(
            "Return only threads changed since this Gmail historyId (incremental "
            "delta via users.history.list). Omit for a normal query."
        ),
    )


class InboxSearchResult(BaseModel):
    items: list[InboxSearchItem]
    current_history_id: str | None = Field(
        default=None,
        description="The mailbox's latest historyId, to pass as a future watermark.",
    )
