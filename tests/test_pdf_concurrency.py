"""Genuine-concurrency test for the document state machine's CAS guarantee.

The rest of the pdf suites run on a single shared in-memory connection, which
serializes access at the Python level and cannot exhibit the race the
compare-and-set in ``update_document`` exists to prevent. This module uses a
file-backed SQLite database with per-thread connections so multiple writers
genuinely interleave: all threads read ``awaiting_signature``, race the
transition, and exactly one may win.
"""

from __future__ import annotations

import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from models.pdf_forms import PdfDocStatus
from services import pdf_documents_repo as repo
from tests.pdf_fixtures import make_flat_pdf
from tests.test_template import TestTemplate

_RACERS = 4


class TestConcurrentSignTransition(TestTemplate):
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pdf-cas-race-")
        db_path = Path(self._tmp.name) / "race.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 15})
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patch = patch.object(repo, "use_db_session", _ctx)
        self._patch.start()

    def teardown_method(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_only_one_racing_sign_wins(self):
        doc = repo.create_document(
            user_id="u1",
            filename="race.pdf",
            data=make_flat_pdf(),
            page_count=1,
        )
        repo.update_document(
            doc.doc_id, "u1", new_status=PdfDocStatus.AWAITING_SIGNATURE
        )

        barrier = threading.Barrier(_RACERS)
        outcomes: list[str] = []
        lock = threading.Lock()

        def racer(tag: int) -> None:
            barrier.wait()
            try:
                repo.update_document(
                    doc.doc_id,
                    "u1",
                    new_status=PdfDocStatus.SIGNED,
                    data=f"%PDF-racer-{tag}".encode(),
                    audit_event={"event": "signed", "racer": tag},
                )
                result = f"won:{tag}"
            except repo.PdfInvalidTransitionError:
                result = f"lost:{tag}"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=racer, args=(i,)) for i in range(_RACERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        winners = [o for o in outcomes if o.startswith("won")]
        assert len(outcomes) == _RACERS
        assert len(winners) == 1, f"exactly one racer must seal: {outcomes}"

        final = repo.load_document(doc.doc_id, "u1")
        assert final.status == PdfDocStatus.SIGNED
        # The stored bytes and audit belong to the single winner - the losers
        # must not have overwritten them.
        winner_tag = winners[0].split(":")[1]
        assert final.current_bytes == f"%PDF-racer-{winner_tag}".encode()
        signed_events = [e for e in (final.audit or []) if e.get("event") == "signed"]
        assert len(signed_events) == 1
        assert str(signed_events[0]["racer"]) == winner_tag
