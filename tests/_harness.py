"""Shared test infrastructure used across the suite.

These helpers used to be copy-pasted per test file (``_patch_db`` existed in
ten files); import them from here instead of redefining them locally.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import token_encryption
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base


@contextmanager
def patch_db():
    """Wire an in-memory SQLite into ``db.engine`` for the duration of the block.

    Yields the session factory so tests can seed rows directly; production code
    inside the block reaches the same database via ``db_engine.use_db_session``.
    """
    orig_engine = db_engine._engine
    orig_session = db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    try:
        yield session_factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


@contextmanager
def plaintext_encryption():
    """Force ``PlaintextEncryption`` everywhere so no Fernet key is needed.

    Patches both the ``services.webhooks_svc`` import-site binding and the
    ``common.token_encryption`` module attribute (which call-time importers
    like ``services.gmail_svc`` resolve), so every consumer sees plaintext.
    """
    enc = PlaintextEncryption()
    with (
        patch("services.webhooks_svc.require_encryption", return_value=enc),
        patch.object(token_encryption, "require_encryption", return_value=enc),
    ):
        yield


def read_sse_first_message(response) -> dict:
    """Parse the first ``data:`` line from an MCP SSE response.

    Deliberately NOT ``iter_lines()``: that splits on Unicode line
    boundaries (U+2028, NEL, ...) which legally appear unescaped inside
    JSON string payloads (e.g. the pdf_signer app bundle's inlined pdf.js
    worker), truncating the frame. The SSE spec ends lines on CR/LF only.
    """
    normalized = response.text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.split("\n"):
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("no SSE data frame in response")
