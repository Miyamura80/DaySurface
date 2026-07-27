"""Tests for the inbox curation ledger (US-002 .. US-011).

The ledger persistence (``services.curation_ledger``) is exercised against an
in-memory SQLite DB, and the three ledger services
(``services.inbox_curation_svc``) are exercised with the Gmail-touching helpers
patched out - so the tests cover the real DB access, encryption round-trip,
freshness (historyId) logic, coverage counting, and provisional-prior wiring
without hitting ``googleapiclient``.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import token_encryption
from common.token_encryption import FernetEncryption
from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from db.models.thread_curation import ThreadCuration
from models.curation import (
    CoverageSummary,
    CurationBucket,
    CurationRecord,
    CurationState,
    GetCurationInput,
    GetCurationResult,
    InboxSearchInput,
    LedgerStatus,
    SaveCurationInput,
    SaveCurationResult,
    SuggestedAction,
    ThreadJudgment,
)
from models.gmail import GmailDisconnectInput
from services import discover_services, get_registry
from services.curation_ledger import (
    list_records,
    mark_state,
    mark_state_best_effort,
    purge_user,
    upsert_judgments,
)
from services.gmail_drafts_svc import GmailReplyInput, gmail_reply_to_thread
from services.gmail_messages_svc import (
    GmailThreadModifyInput,
    gmail_archive_thread,
    gmail_mark_thread_done,
)
from services.gmail_svc import gmail_disconnect
from services.inbox_curation_svc import (
    _changed_thread_ids,
    _list_thread_stubs,
    _mailbox_history_id,
    _search_thread_ids,
    inbox_get_curation,
    inbox_save_curation,
    inbox_search,
)
from tests.test_template import TestTemplate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@contextmanager
def _patch_db():
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
def _patch_fernet():
    """Force a real Fernet backend so encryption-at-rest is actually exercised."""
    enc = FernetEncryption(Fernet.generate_key().decode())
    with patch.object(token_encryption, "require_encryption", return_value=enc):
        yield enc


def _judgment(thread_id: str, **kw) -> ThreadJudgment:
    base: dict = {
        "thread_id": thread_id,
        "bucket": CurationBucket.needs_reply,
        "importance": 0.9,
        "summary": f"summary for {thread_id}",
        "reasoning": f"reasoning for {thread_id}",
        "suggested_action": SuggestedAction.reply,
    }
    base.update(kw)
    return ThreadJudgment.model_validate(base)


def _stub(thread_id: str, history_id: str) -> dict:
    return {"id": thread_id, "historyId": history_id}


# ---------------------------------------------------------------------------
# US-003: contracts validate
# ---------------------------------------------------------------------------


class TestServiceRegistration(TestTemplate):
    """Guard against the @service decorator landing on the wrong function - e.g.
    a refactor inserting a helper between the decorator and the service it was
    meant to decorate. The registered ``func`` must BE the service (callable
    with the input model), not a helper with a different signature."""

    def test_registered_funcs_match_their_services(self):
        discover_services()
        by_name = {e.name: e for e in get_registry()}
        assert by_name["inbox_get_curation"].func is inbox_get_curation
        assert by_name["inbox_search"].func is inbox_search
        assert by_name["inbox_save_curation"].func is inbox_save_curation
        # Input models are wired to the matching service, too.
        assert by_name["inbox_search"].input_model is InboxSearchInput
        assert by_name["inbox_get_curation"].input_model is GetCurationInput
        assert by_name["inbox_save_curation"].input_model is SaveCurationInput


class TestCurationContracts(TestTemplate):
    def test_record_defaults(self):
        rec = CurationRecord(thread_id="t1")
        assert rec.suggested_action == SuggestedAction.none
        assert rec.ledger_status == LedgerStatus.curated

    def test_get_result_coverage_shape(self):
        res = GetCurationResult(
            records=[], coverage=CoverageSummary(curated=1, stale=2)
        )
        assert res.coverage.uncurated == 0
        assert res.coverage.stale == 2

    def test_save_and_search_io(self):
        assert (
            SaveCurationInput(judgments=[_judgment("t1")]).judgments[0].thread_id
            == "t1"
        )
        assert SaveCurationResult(saved=1, thread_ids=["t1"]).saved == 1
        assert InboxSearchInput(query="from:a", limit=5).limit == 5


# ---------------------------------------------------------------------------
# US-002: encryption at rest round-trips; plaintext never persisted
# ---------------------------------------------------------------------------


class TestEncryptionAtRest(TestTemplate):
    def test_round_trip_encrypt_store_decrypt(self):
        with _patch_db() as factory, _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            # Ciphertext on disk must NOT contain the plaintext.
            row = factory().query(ThreadCuration).one()
            assert row.summary_enc is not None
            assert b"summary for t1" not in row.summary_enc
            assert row.key_id != "plaintext"
            # Reading back decrypts.
            recs = list_records("alice")
            assert recs[0].summary == "summary for t1"
            assert recs[0].reasoning == "reasoning for t1"


# ---------------------------------------------------------------------------
# US-006: inbox_save_curation upsert semantics
# ---------------------------------------------------------------------------


class TestSaveCuration(TestTemplate):
    def _run_save(self, judgments, history_map):
        svc = MagicMock()
        with (
            _patch_db(),
            _patch_fernet(),
            patch("services.inbox_curation_svc._get_gmail_client", return_value=svc),
            patch(
                "services.inbox_curation_svc._batch_get_threads",
                return_value={
                    tid: {"id": tid, "historyId": h} for tid, h in history_map.items()
                },
            ),
        ):
            return inbox_save_curation(
                SaveCurationInput(
                    user_id="alice", judgments=judgments, curator_version="v-test"
                )
            )

    def test_insert_new(self):
        with _patch_db(), _patch_fernet():
            svc = MagicMock()
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client", return_value=svc
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value={"t1": {"id": "t1", "historyId": "100"}},
                ),
            ):
                res = inbox_save_curation(
                    SaveCurationInput(user_id="alice", judgments=[_judgment("t1")])
                )
            assert res.saved == 1
            recs = list_records("alice")
            assert recs[0].curated_history_id == "100"
            assert recs[0].state == CurationState.curated

    def test_update_existing_advances_history(self):
        with _patch_db(), _patch_fernet():
            svc = MagicMock()
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client",
                    return_value=svc,
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value={"t1": {"id": "t1", "historyId": "100"}},
                ),
            ):
                inbox_save_curation(
                    SaveCurationInput(user_id="alice", judgments=[_judgment("t1")])
                )
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client",
                    return_value=svc,
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value={"t1": {"id": "t1", "historyId": "205"}},
                ),
            ):
                inbox_save_curation(
                    SaveCurationInput(
                        user_id="alice",
                        judgments=[_judgment("t1", summary="updated")],
                    )
                )
            recs = list_records("alice")
            assert len(recs) == 1  # upsert, not duplicate
            assert recs[0].curated_history_id == "205"
            assert recs[0].summary == "updated"

    def test_batch_mixed_insert_update(self):
        res = self._run_save(
            [_judgment("t1"), _judgment("t2")],
            {"t1": "100", "t2": "101"},
        )
        assert isinstance(res, SaveCurationResult)
        assert res.saved == 2
        assert set(res.thread_ids) == {"t1", "t2"}

    def test_empty_batch_noop(self):
        with _patch_db(), _patch_fernet():
            with patch("services.inbox_curation_svc._get_gmail_client") as get_client:
                res = inbox_save_curation(
                    SaveCurationInput(user_id="alice", judgments=[])
                )
            assert res.saved == 0
            get_client.assert_not_called()  # no Gmail call for an empty batch

    def test_recurate_preserves_watermark_when_history_missing(self):
        with _patch_db(), _patch_fernet():
            svc = MagicMock()
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client", return_value=svc
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value={"t1": {"id": "t1", "historyId": "100"}},
                ),
            ):
                inbox_save_curation(
                    SaveCurationInput(user_id="alice", judgments=[_judgment("t1")])
                )
            # Re-curate, but this time the batch fetch misses t1 (empty map).
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client", return_value=svc
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads", return_value={}
                ),
            ):
                inbox_save_curation(
                    SaveCurationInput(
                        user_id="alice",
                        judgments=[_judgment("t1", summary="updated")],
                    )
                )
            rec = list_records("alice")[0]
            assert rec.summary == "updated"  # the re-curation applied...
            assert rec.curated_history_id == "100"  # ...but the watermark held


# ---------------------------------------------------------------------------
# US-004: inbox_get_curation cheap read + coverage + freshness
# ---------------------------------------------------------------------------


class TestGetCuration(TestTemplate):
    def _run_get(self, stubs, inp=None):
        svc = MagicMock()
        with (
            patch("services.inbox_curation_svc._get_gmail_client", return_value=svc),
            patch("services.inbox_curation_svc._list_thread_stubs", return_value=stubs),
        ):
            return inbox_get_curation(inp or GetCurationInput(user_id="alice"))

    def test_empty_ledger_cold_start(self):
        with _patch_db(), _patch_fernet():
            res = self._run_get([_stub("t1", "100"), _stub("t2", "101")])
            assert res.coverage.curated == 0
            assert res.coverage.stale == 0
            assert res.coverage.uncurated == 2
            assert res.records == []

    def test_fresh_rows_returned(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            res = self._run_get([_stub("t1", "100"), _stub("t2", "101")])
            assert res.coverage.curated == 1
            assert res.coverage.uncurated == 1
            assert res.coverage.stale == 0
            assert len(res.records) == 1
            assert res.records[0].ledger_status == LedgerStatus.curated

    def test_stale_detection(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            # Thread t1's current historyId advanced -> stale.
            res = self._run_get([_stub("t1", "999")])
            assert res.coverage.stale == 1
            assert res.coverage.curated == 0
            assert res.records[0].ledger_status == LedgerStatus.stale

    def test_fresh_only_filters_stale(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            res = self._run_get(
                [_stub("t1", "999")],
                GetCurationInput(user_id="alice", fresh_only=True),
            )
            assert res.records == []
            assert res.coverage.stale == 1

    def test_thread_left_inbox_is_stale(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            # t1 no longer in the inbox stub set (archived out of band).
            res = self._run_get([_stub("t2", "200")])
            assert res.records[0].ledger_status == LedgerStatus.stale
            assert res.coverage.uncurated == 1  # only t2 counts against inbox

    def test_check_freshness_false_treats_all_as_curated(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            # historyId advanced, but the freshness check is disabled.
            res = self._run_get(
                [_stub("t1", "999")],
                GetCurationInput(user_id="alice", check_freshness=False),
            )
            assert res.coverage.stale == 0
            assert res.coverage.curated == 1
            assert res.records[0].ledger_status == LedgerStatus.curated

    def test_bucket_filter_scopes_records_not_coverage(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments(
                "alice",
                [
                    _judgment("t1", bucket=CurationBucket.needs_reply),
                    _judgment("t2", bucket=CurationBucket.fyi),
                ],
                history_ids={"t1": "1", "t2": "2"},
            )
            res = self._run_get(
                [_stub("t1", "1"), _stub("t2", "2")],
                GetCurationInput(user_id="alice", bucket=CurationBucket.fyi),
            )
            # Records are filtered to the requested bucket...
            assert [r.thread_id for r in res.records] == ["t2"]
            # ...but coverage still reflects the whole inbox vs the whole ledger.
            assert res.coverage.curated == 2
            assert res.coverage.uncurated == 0


# ---------------------------------------------------------------------------
# US-005 + US-008: inbox_search annotation + provisional prior
# ---------------------------------------------------------------------------


class TestInboxSearch(TestTemplate):
    def _fetched(self, ids_to_hist):
        out = {}
        for tid, hist in ids_to_hist.items():
            out[tid] = {
                "id": tid,
                "historyId": hist,
                "messages": [
                    {
                        "id": f"m-{tid}",
                        "labelIds": ["INBOX", "UNREAD"],
                        "internalDate": "1700000000000",
                        "snippet": f"snippet {tid}",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "a@x.com"},
                                {"name": "Subject", "value": f"subj {tid}"},
                            ]
                        },
                    }
                ],
            }
        return out

    def test_mixed_fresh_stale_uncurated(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments(
                "alice",
                [_judgment("fresh"), _judgment("stale")],
                history_ids={"fresh": "100", "stale": "100"},
            )
            svc = MagicMock()
            fetched = self._fetched({"fresh": "100", "stale": "999", "new": "50"})
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client",
                    return_value=svc,
                ),
                patch(
                    "services.inbox_curation_svc._build_label_lookups",
                    return_value=({}, {}),
                ),
                patch(
                    "services.inbox_curation_svc._search_thread_ids",
                    return_value=["fresh", "stale", "new"],
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value=fetched,
                ),
                patch(
                    "services.inbox_curation_svc._find_mcp_done_label",
                    return_value=None,
                ),
                patch(
                    "services.inbox_curation_svc._mailbox_history_id",
                    return_value="1000",
                ),
            ):
                res = inbox_search(InboxSearchInput(user_id="alice"))
            by_id = {i.thread_id: i for i in res.items}
            assert by_id["fresh"].ledger_status == LedgerStatus.curated
            assert by_id["stale"].ledger_status == LedgerStatus.stale
            assert by_id["new"].ledger_status == LedgerStatus.uncurated
            # US-008: only the uncurated thread carries a provisional prior.
            assert by_id["new"].importance_prior is not None
            assert by_id["fresh"].importance_prior is None
            assert res.current_history_id == "1000"


# ---------------------------------------------------------------------------
# US-007: action services update the ledger
# ---------------------------------------------------------------------------


class TestActionsUpdateLedger(TestTemplate):
    def test_archive_marks_dismissed(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            svc = MagicMock()
            with patch(
                "services.gmail_messages_svc._get_gmail_client", return_value=svc
            ):
                gmail_archive_thread(
                    GmailThreadModifyInput(user_id="alice", thread_id="t1")
                )
            recs = list_records("alice")
            assert recs[0].state == CurationState.dismissed

    def test_reply_marks_acted_with_draft(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            svc = MagicMock()
            svc.users().threads().get().execute.return_value = {
                "messages": [
                    {
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "other@x.com"},
                                {"name": "Subject", "value": "Hi"},
                                {"name": "Message-ID", "value": "<abc@x>"},
                            ]
                        }
                    }
                ]
            }
            svc.users().drafts().create().execute.return_value = {"id": "draft-9"}
            with (
                patch("services.gmail_drafts_svc._get_gmail_client", return_value=svc),
                patch(
                    "services.gmail_drafts_svc._fetch_draft_model",
                    return_value=MagicMock(),
                ),
                patch(
                    "services.gmail_drafts_svc._account_email", return_value="me@x.com"
                ),
            ):
                gmail_reply_to_thread(
                    GmailReplyInput(
                        user_id="alice", thread_id="t1", body="hello", to="other@x.com"
                    )
                )
            recs = list_records("alice")
            assert recs[0].state == CurationState.acted
            assert recs[0].draft_id == "draft-9"

    def test_mark_done_marks_dismissed(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "100"})
            svc = MagicMock()
            svc.users().labels().list().execute.return_value = {
                "labels": [{"id": "L", "name": "MCP/Done"}]
            }
            with patch(
                "services.gmail_messages_svc._get_gmail_client", return_value=svc
            ):
                gmail_mark_thread_done(
                    GmailThreadModifyInput(user_id="alice", thread_id="t1")
                )
            assert list_records("alice")[0].state == CurationState.dismissed

    def test_mark_state_noop_for_uncurated(self):
        with _patch_db(), _patch_fernet():
            # No ledger row for t-missing -> mark_state returns False, no row made.
            assert mark_state("alice", "t-missing", CurationState.acted) is False
            assert list_records("alice") == []

    def test_mark_state_best_effort_swallows_db_error(self):
        # A DB failure during a ledger update must not propagate out of an action
        # tool that already succeeded against Gmail.
        with patch(
            "services.curation_ledger.mark_state",
            side_effect=SQLAlchemyError("db down"),
        ):
            mark_state_best_effort("alice", "t1", CurationState.acted)  # no raise


# ---------------------------------------------------------------------------
# US-011: purge on disconnect
# ---------------------------------------------------------------------------


class TestPurge(TestTemplate):
    def test_purge_user_removes_rows(self):
        with _patch_db(), _patch_fernet():
            upsert_judgments(
                "alice",
                [_judgment("t1"), _judgment("t2")],
                history_ids={"t1": "1", "t2": "2"},
            )
            upsert_judgments("bob", [_judgment("t3")], history_ids={"t3": "3"})
            deleted = purge_user("alice")
            assert deleted == 2
            assert list_records("alice") == []
            assert len(list_records("bob")) == 1  # other users untouched

    def test_disconnect_purges_ledger(self):
        with _patch_db() as factory, _patch_fernet():
            s = factory()
            s.add(
                GoogleToken(
                    user_id="alice",
                    email="alice@x.com",
                    refresh_token_enc=b"RT",
                    key_id="plaintext",
                )
            )
            s.commit()
            upsert_judgments("alice", [_judgment("t1")], history_ids={"t1": "1"})
            with patch("services.gmail_svc.httpx.post"):
                gmail_disconnect(GmailDisconnectInput(user_id="alice"))
            assert list_records("alice") == []

    def test_disconnect_survives_purge_failure(self):
        # The token is already revoked+committed before the purge runs, so a
        # purge DB error must not turn a successful disconnect into an error.
        with _patch_db() as factory, _patch_fernet():
            s = factory()
            s.add(
                GoogleToken(
                    user_id="alice",
                    email="alice@x.com",
                    refresh_token_enc=b"RT",
                    key_id="plaintext",
                )
            )
            s.commit()
            with (
                patch("services.gmail_svc.httpx.post"),
                patch(
                    "services.curation_ledger.purge_user",
                    side_effect=SQLAlchemyError("db down"),
                ),
            ):
                result = gmail_disconnect(GmailDisconnectInput(user_id="alice"))
            assert result.revoked is True  # disconnect still reported success


# ---------------------------------------------------------------------------
# Gmail-shape helpers: exercise the real response parsing (no helper mocking).
# These feed canned Gmail API payloads through a mocked client, the same
# pattern as tests/test_gmail_services.py, so a wrong field name / pagination
# bug would actually fail.
# ---------------------------------------------------------------------------


class TestGmailShapeHelpers(TestTemplate):
    def test_list_thread_stubs_paginates(self):
        svc = MagicMock()
        svc.users().threads().list().execute.side_effect = [
            {
                "threads": [
                    {"id": "a", "historyId": "1"},
                    {"id": "b", "historyId": "2"},
                ],
                "nextPageToken": "p2",
            },
            {"threads": [{"id": "c", "historyId": "3"}]},
        ]
        stubs = _list_thread_stubs(svc, "in:inbox", cap=100)
        assert [s["id"] for s in stubs] == ["a", "b", "c"]

    def test_list_thread_stubs_respects_cap(self):
        svc = MagicMock()
        svc.users().threads().list().execute.side_effect = [
            {
                "threads": [
                    {"id": "a", "historyId": "1"},
                    {"id": "b", "historyId": "2"},
                ],
                "nextPageToken": "p2",
            },
            {"threads": [{"id": "c", "historyId": "3"}]},  # must NOT be fetched
        ]
        stubs = _list_thread_stubs(svc, "in:inbox", cap=2)
        assert [s["id"] for s in stubs] == ["a", "b"]

    def test_search_thread_ids_parses_and_scopes_query(self):
        svc = MagicMock()
        svc.users().threads().list().execute.return_value = {
            "threads": [{"id": "x"}, {"id": "y"}, {"no_id": True}]
        }
        ids = _search_thread_ids(svc, "from:vip@x.com", 10)
        assert ids == ["x", "y"]
        # Query is scoped to the triageable-inbox base AND the caller filter.
        q = svc.users().threads().list.call_args.kwargs["q"]
        assert "in:inbox" in q
        assert "from:vip@x.com" in q

    def test_mailbox_history_id_success_and_failure(self):
        svc = MagicMock()
        svc.users().getProfile().execute.return_value = {"historyId": 500}
        assert _mailbox_history_id(svc) == "500"

        broken = MagicMock()
        broken.users().getProfile().execute.side_effect = RuntimeError("boom")
        assert _mailbox_history_id(broken) is None  # best-effort, never raises

    def test_changed_thread_ids_dedupes_and_captures_all_change_types(self):
        # Each history record's ``messages`` lists every touched message,
        # including label-only changes (t3 here has no new message) - so the
        # delta stays aligned with historyId freshness.
        svc = MagicMock()
        svc.users().history().list().execute.side_effect = [
            {
                "historyId": "900",
                "history": [
                    {
                        "messages": [{"threadId": "t1"}, {"threadId": "t2"}],
                        "messagesAdded": [{"message": {"threadId": "t1"}}],
                    }
                ],
                "nextPageToken": "pg2",
            },
            {
                "historyId": "950",
                "history": [
                    {
                        # Label-only change: present in `messages`, no messagesAdded.
                        "messages": [{"threadId": "t2"}, {"threadId": "t3"}],
                        "labelsAdded": [
                            {"message": {"threadId": "t3"}, "labelIds": ["UNREAD"]}
                        ],
                    }
                ],
            },
        ]
        ids, latest = _changed_thread_ids(svc, "800")
        assert ids == ["t1", "t2", "t3"]  # order preserved, deduped
        assert latest == "950"
        # No historyTypes filter is sent (all change types are returned).
        assert "historyTypes" not in svc.users().history().list.call_args.kwargs

    def test_changed_thread_ids_404_falls_back(self):
        svc = MagicMock()
        svc.users().history().list().execute.side_effect = HttpError(
            resp=MagicMock(status=404, reason="Not Found"), content=b""
        )
        # historyId too old -> (None, None) so the caller runs a normal query.
        assert _changed_thread_ids(svc, "1") == (None, None)


class TestInboxSearchIncremental(TestTemplate):
    """Drive inbox_search's since_history_id path with the REAL
    _changed_thread_ids (only the client + batch fetch are mocked)."""

    def _fetched(self, tid: str, hist: str) -> dict:
        return {
            "id": tid,
            "historyId": hist,
            "messages": [
                {
                    "id": f"m-{tid}",
                    "labelIds": ["INBOX"],
                    "internalDate": "1700000000000",
                    "snippet": f"snippet {tid}",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "a@x.com"},
                            {"name": "Subject", "value": f"subj {tid}"},
                        ]
                    },
                }
            ],
        }

    def test_since_history_id_uses_history_delta(self):
        with _patch_db(), _patch_fernet():
            svc = MagicMock()
            svc.users().history().list().execute.return_value = {
                "historyId": "999",
                "history": [{"messages": [{"threadId": "t1"}, {"threadId": "t2"}]}],
            }
            fetched = {
                "t1": self._fetched("t1", "999"),
                "t2": self._fetched("t2", "999"),
            }
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client", return_value=svc
                ),
                patch(
                    "services.inbox_curation_svc._build_label_lookups",
                    return_value=({}, {}),
                ),
                patch(
                    "services.inbox_curation_svc._find_mcp_done_label",
                    return_value=None,
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value=fetched,
                ),
            ):
                res = inbox_search(
                    InboxSearchInput(user_id="alice", since_history_id="800")
                )
            assert {i.thread_id for i in res.items} == {"t1", "t2"}
            # Watermark comes from history.list, not a getProfile fallback.
            assert res.current_history_id == "999"

    def _msg(self, labels: list[str]) -> dict:
        return {
            "id": "m",
            "labelIds": labels,
            "internalDate": "1700000000000",
            "snippet": "s",
            "payload": {"headers": [{"name": "Subject", "value": "s"}]},
        }

    def test_incremental_filters_non_triageable_threads(self):
        # The unfiltered history delta can include archived/done threads after a
        # label-only change; they must be dropped so inbox_search stays scoped to
        # the triageable inbox.
        with _patch_db(), _patch_fernet():
            svc = MagicMock()
            svc.users().history().list().execute.return_value = {
                "historyId": "999",
                "history": [
                    {
                        "messages": [
                            {"threadId": "keep"},
                            {"threadId": "archived"},
                            {"threadId": "done"},
                        ]
                    }
                ],
            }
            fetched = {
                "keep": {
                    "id": "keep",
                    "historyId": "1",
                    "messages": [self._msg(["INBOX"])],
                },
                "archived": {
                    "id": "archived",
                    "historyId": "1",
                    "messages": [self._msg([])],
                },
                "done": {
                    "id": "done",
                    "historyId": "1",
                    "messages": [self._msg(["INBOX", "DONE_ID"])],
                },
            }
            with (
                patch(
                    "services.inbox_curation_svc._get_gmail_client", return_value=svc
                ),
                patch(
                    "services.inbox_curation_svc._build_label_lookups",
                    return_value=({}, {}),
                ),
                patch(
                    "services.inbox_curation_svc._find_mcp_done_label",
                    return_value="DONE_ID",
                ),
                patch(
                    "services.inbox_curation_svc._batch_get_threads",
                    return_value=fetched,
                ),
            ):
                res = inbox_search(
                    InboxSearchInput(user_id="alice", since_history_id="800")
                )
            assert {i.thread_id for i in res.items} == {"keep"}
