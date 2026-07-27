"""Migration-backed integration test for the thread_curation ledger.

Unlike the unit tests (which build the schema with ``Base.metadata.create_all``
from the ORM), this test runs the *real* Alembic migration ``009`` end-to-end
against a fresh database and then round-trips a row through the migrated schema.
That is what catches migration-vs-ORM drift: a column the migration forgot, a
type mismatch, or a missing index would pass ``create_all`` tests but fail here.

``alembic upgrade head`` is run in a subprocess (the same command CI runs) with
``BACKEND_DB_URI`` pointed at a temp SQLite file, so the whole 001..009 chain is
exercised, not just 009 in isolation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from db.models.thread_curation import ThreadCuration
from tests.test_template import slow_test

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_upgrade(db_uri: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        env={
            "BACKEND_DB_URI": db_uri,
            "DEV_ENV": "local",
            "PATH": os.environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


@slow_test
def test_migration_009_creates_thread_curation_matching_orm(tmp_path):
    db_path = tmp_path / "ledger_mig.db"
    db_uri = f"sqlite:///{db_path}"

    proc = _alembic_upgrade(db_uri)
    if proc.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{proc.stdout}\n{proc.stderr}")

    engine = create_engine(db_uri)
    inspector = inspect(engine)

    # 1. The migrated table matches the ORM per column - name, type, and
    #    nullability - so a type/length/nullable drift (not just a missing
    #    column) fails here too. Types are compared by their compiled SQL string
    #    (e.g. "VARCHAR(32)", "FLOAT") under the same dialect.
    def _profile_migrated() -> dict[str, tuple[str, bool]]:
        return {
            c["name"]: (str(c["type"]), bool(c["nullable"]))
            for c in inspector.get_columns("thread_curation")
        }

    def _profile_orm() -> dict[str, tuple[str, bool]]:
        dialect = engine.dialect
        return {
            c.name: (str(c.type.compile(dialect=dialect)), bool(c.nullable))
            for c in ThreadCuration.__table__.columns
        }

    migrated_profile = _profile_migrated()
    orm_profile = _profile_orm()
    assert migrated_profile == orm_profile, (
        "migration/ORM column drift (name -> (type, nullable)): "
        f"migration={migrated_profile}, orm={orm_profile}"
    )

    # 2. The declared indexes exist.
    index_names = {i["name"] for i in inspector.get_indexes("thread_curation")}
    assert {
        "ix_thread_curation_user_state",
        "ix_thread_curation_user_bucket",
    } <= index_names

    # 3. A row round-trips through the ORM against the *migrated* schema.
    session_local = sessionmaker(bind=engine, expire_on_commit=False)
    with session_local() as s:
        s.add(
            ThreadCuration(
                user_id="u1",
                thread_id="t1",
                bucket="needs_reply",
                importance=0.9,
                summary_enc=b"ciphertext",
                key_id="v1",
                suggested_action="reply",
                state="curated",
                curated_history_id="100",
                curator_version="v-test",
            )
        )
        s.commit()

    with session_local() as s:
        row = s.query(ThreadCuration).one()
        assert (row.user_id, row.thread_id) == ("u1", "t1")
        assert row.state == "curated"
        assert row.curated_history_id == "100"
        assert row.summary_enc == b"ciphertext"

    engine.dispose()
