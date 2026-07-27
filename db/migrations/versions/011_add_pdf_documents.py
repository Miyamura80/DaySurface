"""Add pdf_documents table for server-side PDF form/signing sessions.

Stores one row per open document session. PDF bytes stay server-side (never
in the model's context); LLM-visible tools operate on the ``doc_id`` handle.
No foreign keys into Gmail data - the source locator is opaque JSON so the
PDF domain remains extractable as a standalone add-on.

Revision ID: 011
Revises: 010
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pdf_documents",
        sa.Column("doc_id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("original_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("current_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ref", sa.JSON(), nullable=True),
        sa.Column("placement", sa.JSON(), nullable=True),
        sa.Column("audit", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_pdf_documents_user_id", "pdf_documents", ["user_id"])
    # Index for the TTL retention sweep (DELETE WHERE updated_at < cutoff).
    op.create_index("ix_pdf_documents_updated_at", "pdf_documents", ["updated_at"])


def downgrade() -> None:
    op.drop_table("pdf_documents")
