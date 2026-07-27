"""Disconnect must leave no banked email content behind (fast tier).

``gmail_disconnect`` erases the Google refresh-token ciphertext instead of
only stamping ``revoked_at``, and purges ``webhook_events`` - whose payloads
bank the subject / sender / snippet of every notified message as plaintext
JSON - alongside their pending deliveries. The curation-ledger half of the
same purge is covered in ``tests/test_inbox_curation.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import token_encryption
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from models.gmail import GmailDisconnectInput, GmailStatusInput
from models.webhooks import WebhookSubscribeInput
from services.gmail_svc import gmail_disconnect, gmail_status
from services.webhooks_svc import (
    enqueue_event,
    purge_user_events,
    webhook_subscribe,
)
from tests.test_template import TestTemplate


@contextmanager
def _patch_db():
    """Wire an in-memory SQLite into db.engine for the duration of a test."""
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
def _plaintext_encryption():
    """Force PlaintextEncryption everywhere so no Fernet key is needed."""
    enc = PlaintextEncryption()
    with (
        patch("services.webhooks_svc.require_encryption", return_value=enc),
        patch.object(token_encryption, "require_encryption", return_value=enc),
    ):
        yield


class TestPurgeOnDisconnect(TestTemplate):
    """Disconnecting must leave no banked email content behind.

    ``webhook_events.payload`` holds the subject / sender / snippet of every
    notified message as plaintext JSON, so it is purged alongside the curation
    ledger - and the refresh-token ciphertext is erased, not just flagged.
    """

    @staticmethod
    def _seed_user(user_id: str) -> str:
        """Subscribe + enqueue one event for ``user_id``; return the sub id."""
        res = webhook_subscribe(
            WebhookSubscribeInput(user_id=user_id, url=f"https://{user_id}.test/hook")
        )
        with db_engine.use_db_session() as session:
            enqueue_event(
                session,
                user_id=user_id,
                event_type="gmail.message.new",
                payload={"subject": "Secret subject", "snippet": "secret snippet"},
            )
            session.commit()
        return res.id

    @staticmethod
    def _seed_token(factory, user_id: str) -> None:
        session = factory()
        session.add(
            GoogleToken(
                user_id=user_id,
                email=f"{user_id}@x.com",
                refresh_token_enc=b"RT",
                key_id="plaintext",
            )
        )
        session.commit()

    def test_purge_user_events_removes_events_and_deliveries(self):
        with _patch_db(), _plaintext_encryption():
            self._seed_user("u1")
            self._seed_user("u2")

            events, deliveries = purge_user_events("u1")
            assert (events, deliveries) == (1, 1)

            with db_engine.use_db_session() as session:
                assert session.query(WebhookEvent).count() == 1  # u2 untouched
                assert session.query(WebhookEvent).one().user_id == "u2"
                assert session.query(WebhookDelivery).count() == 1
                # Subscriptions carry no email content and must survive.
                assert session.query(WebhookSubscription).count() == 2

    def test_delivery_purge_matches_on_a_subquery_not_an_id_list(self):
        # A busy mailbox banks one event per message. If the delivery delete
        # expanded those ids into an IN (...) list it would eventually exceed
        # the driver's bind-parameter limit, and the best-effort caller would
        # swallow the error and leave payloads behind. Pin the subquery form:
        # the emitted SQL must not carry one parameter per event.
        statements: list[tuple[str, int]] = []

        with _patch_db(), _plaintext_encryption():
            self._seed_user("busy")
            with db_engine.use_db_session() as session:
                for i in range(9):  # 10 events total for this user
                    enqueue_event(
                        session,
                        user_id="busy",
                        event_type="gmail.message.new",
                        payload={"subject": f"msg {i}"},
                    )
                session.commit()

            def record(_conn, _cur, statement, parameters, _ctx, _many):  # noqa: ANN202
                statements.append((statement, len(parameters or ())))

            event.listen(db_engine._engine, "before_cursor_execute", record)
            events, deliveries = purge_user_events("busy")

        assert (events, deliveries) == (10, 10)
        deletes = [s for s in statements if s[0].lstrip().upper().startswith("DELETE")]
        delivery_delete = next(s for s in deletes if "webhook_deliveries" in s[0])
        assert "SELECT" in delivery_delete[0].upper()  # subquery, not a list
        assert delivery_delete[1] == 1  # the user_id bind, nothing per-event

    def test_disconnect_purges_webhook_events_and_erases_token(self):
        with _patch_db() as factory, _plaintext_encryption():
            self._seed_token(factory, "u1")
            self._seed_token(factory, "u2")
            self._seed_user("u1")
            self._seed_user("u2")

            with patch("services.gmail_svc.httpx.post"):
                result = gmail_disconnect(GmailDisconnectInput(user_id="u1"))

            assert result.revoked is True
            with db_engine.use_db_session() as session:
                rows = {r.user_id: r for r in session.query(GoogleToken).all()}
                # The ciphertext is gone, not merely flagged revoked.
                assert rows["u1"].refresh_token_enc is None
                assert rows["u1"].revoked_at is not None
                assert rows["u2"].refresh_token_enc == b"RT"  # other user intact

                remaining = session.query(WebhookEvent).all()
                assert [e.user_id for e in remaining] == ["u2"]
                assert session.query(WebhookDelivery).count() == 1

    def test_disconnect_cleans_up_an_already_revoked_connection(self):
        # The purge is best-effort, and it did not exist before this change, so
        # a user can hold banked payloads behind an already-revoked row. A
        # second disconnect must still reach them: revoked=False (nothing live
        # to revoke) but the leftovers are gone.
        with _patch_db() as factory, _plaintext_encryption():
            session = factory()
            session.add(
                GoogleToken(
                    user_id="u1",
                    email="u1@x.com",
                    refresh_token_enc=None,
                    key_id="plaintext",
                    revoked_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            session.commit()
            self._seed_user("u1")  # events banked while the row was revoked

            with patch("services.gmail_svc.httpx.post") as post:
                result = gmail_disconnect(GmailDisconnectInput(user_id="u1"))

            assert result.revoked is False  # nothing live was revoked
            post.assert_not_called()  # and no pointless call to Google
            with db_engine.use_db_session() as session:
                assert session.query(WebhookEvent).count() == 0
                assert session.query(WebhookDelivery).count() == 0

    def test_erased_row_reads_as_disconnected_everywhere(self):
        # A NULL ciphertext is not a usable connection: status must not report
        # it as connected while the client would fail closed on it.
        with _patch_db() as factory, _plaintext_encryption():
            self._seed_token(factory, "u1")
            with patch("services.gmail_svc.httpx.post"):
                gmail_disconnect(GmailDisconnectInput(user_id="u1"))

            assert gmail_status(GmailStatusInput(user_id="u1")).connected is False

    def test_disconnect_survives_webhook_purge_failure(self):
        # The token revoke is already committed before the purge runs, so a
        # purge DB error must not turn a successful disconnect into an error.
        with _patch_db() as factory, _plaintext_encryption():
            self._seed_token(factory, "u1")
            self._seed_user("u1")

            with (
                patch("services.gmail_svc.httpx.post"),
                patch(
                    "services.webhooks_svc.purge_user_events",
                    side_effect=SQLAlchemyError("db down"),
                ),
            ):
                result = gmail_disconnect(GmailDisconnectInput(user_id="u1"))

            assert result.revoked is True
            with db_engine.use_db_session() as session:
                assert session.query(GoogleToken).one().refresh_token_enc is None
