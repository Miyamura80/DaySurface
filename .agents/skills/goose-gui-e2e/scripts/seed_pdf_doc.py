#!/usr/bin/env python3
"""Seed a pdf_documents session for the pdf_signer e2e scenarios.

Run via ``uv run python .agents/skills/goose-gui-e2e/scripts/seed_pdf_doc.py``
from the repo root with ``BACKEND_DB_URI`` / ``E2E_USER_ID`` in the env (lib.sh
sets them). Inserts (or resets) one open document session with a fixed doc_id
so a scenario's static ``pdf_request_signature`` args can reference it - the
mock LLM cannot thread a runtime doc_id from a prior tool result.

The bytes are a real flat NDA built by the repo's own fixture + edit engine,
so pdf.js inside the Goose iframe renders authentic filled content.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models.pdf_documents import PdfDocument
from models.pdf_forms import AddTextOp
from services.pdf_edit_engine import apply_ops
from tests.pdf_fixtures import make_flat_pdf

DOC_ID = "e2e-nda-doc"

uri = os.environ["BACKEND_DB_URI"]
user_id = os.environ.get("E2E_USER_ID", "e2e-user")

# Two pages: scenarios assert the review renders EVERY page (a signer must
# never sign a document they only saw page 1 of).
filled = apply_ops(
    make_flat_pdf(page_count=2),
    [
        AddTextOp(page=1, x=110.0, y=650.0, text="Eito Miyamura"),
        AddTextOp(page=1, x=105.0, y=600.0, text="2026-07-22"),
        AddTextOp(page=2, x=110.0, y=650.0, text="Eito Miyamura"),
    ],
)

engine = create_engine(uri)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

with Session() as s:
    existing = s.get(PdfDocument, DOC_ID)
    if existing is not None:
        # Reset to a fresh open session so re-runs (or a prior signed run)
        # start from the same state.
        s.delete(existing)
        s.commit()
    s.add(
        PdfDocument(
            doc_id=DOC_ID,
            user_id=user_id,
            status="open",
            filename="nda.pdf",
            original_bytes=filled,
            current_bytes=filled,
            page_count=1,
            source_ref={"type": "gmail_attachment", "message_id": "e2e"},
            audit=[],
        )
    )
    s.commit()

print(DOC_ID)
