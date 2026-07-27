"""Server-side PDF document sessions for form filling and user-gated signing.

One row per open document. The PDF bytes live here - never in the model's
context - and every LLM-visible tool operates on the ``doc_id`` handle. The
``status`` column carries the signing state machine
(``open -> awaiting_signature -> signed``); transitions are validated in
``services/pdf_documents_repo.py``, not here, because the db layer must stay
free of business rules.

Deliberately no foreign keys into Gmail data: the source is an opaque JSON
locator (``source_ref``) so the PDF domain can be extracted as a standalone
add-on later (see the isolation seam in the PRD).
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PdfDocument(Base):
    __tablename__ = "pdf_documents"

    # Human-readable id (human_id.generate_id), per repo convention.
    doc_id: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # open | awaiting_signature | signed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    # Original filename of the source PDF; drives default export naming.
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Bytes as first fetched from the source - kept for audit/diffing.
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Bytes after the latest edit / signature.
    current_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Opaque source locator (e.g. {"type": "gmail_attachment", ...}). JSON, no
    # FK: the PDF core never interprets it - only the bridge module does.
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Requested signature placement, recorded by pdf_request_signature.
    placement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Append-only audit trail: signature ceremony events, aborts, seal record.
    audit: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # index=True mirrors ix_pdf_documents_updated_at in the migration (the
    # TTL-sweep index); without it a future autogenerate would drop the index.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )
