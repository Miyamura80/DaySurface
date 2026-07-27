"""Tests for gmail_watch_svc + the Gmail Pub/Sub push receiver route.

No network or Node: the Gmail client is a MagicMock, OIDC verification and the
history-sync service are patched. Covers watch start persistence, history sync
+ fan-out, Pub/Sub messageId dedup, the 404 expired-baseline full resync, and
the receiver's auth / envelope handling.
"""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.server import app
from common import global_config
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.gmail_push import ProcessedPubsubMessage
from db.models.google_tokens import GoogleToken
from db.models.webhooks import WebhookDelivery
from models.gmail_watch import GmailWatchStartInput
from models.webhooks import WebhookSubscribeInput
from services.gmail_watch_svc import (
    gmail_watch_start,
    process_notification,
    renew_due_watches,
)
from services.webhooks_svc import webhook_subscribe
from tests.test_template import TestTemplate


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
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = factory
    try:
        yield factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


@contextmanager
def _plaintext_encryption():
    with patch(
        "services.webhooks_svc.require_encryption",
        return_value=PlaintextEncryption(),
    ):
        yield


def _seed_token(email="a@b.com", user_id="u1", history_id="100"):
    with db_engine.use_db_session() as session:
        session.add(
            GoogleToken(
                user_id=user_id,
                email=email,
                refresh_token_enc=b"x",
                key_id="plaintext",
                watch_history_id=history_id,
                watch_topic="projects/p/topics/t",
            )
        )
        session.commit()


def _fake_client(**kw) -> MagicMock:
    client = MagicMock()
    users = client.users.return_value
    users.watch.return_value.execute.return_value = kw.get(
        "watch", {"historyId": "123", "expiration": "1799999999000"}
    )
    users.stop.return_value.execute.return_value = {}
    if "history_error" in kw:
        users.history.return_value.list.return_value.execute = MagicMock(
            side_effect=kw["history_error"]
        )
    else:
        users.history.return_value.list.return_value.execute.return_value = kw.get(
            "history", {"history": []}
        )
    users.messages.return_value.get.return_value.execute.return_value = kw.get(
        "message",
        {
            "id": "m1",
            "threadId": "t1",
            "labelIds": ["INBOX"],
            "snippet": "hello there",
            "payload": {"headers": [{"name": "Subject", "value": "Hi"}]},
        },
    )
    users.messages.return_value.list.return_value.execute.return_value = kw.get(
        "messages_list", {"messages": [{"id": "r1"}]}
    )
    return client


def _http_error(status: int) -> HttpError:
    return HttpError(Response({"status": status}), b"{}")


# ---------------------------------------------------------------------------
# Watch lifecycle
# ---------------------------------------------------------------------------


class TestWatchStart(TestTemplate):
    def test_start_persists_history_id_and_expiration(self):
        with (
            _patch_db(),
            patch.object(global_config, "GMAIL_PUBSUB_TOPIC", "projects/p/topics/t"),
            patch(
                "services.gmail_watch_svc._get_gmail_client",
                return_value=_fake_client(),
            ),
        ):
            _seed_token(history_id=None)
            res = gmail_watch_start(GmailWatchStartInput(user_id="u1"))
            assert res.watching is True
            assert res.history_id == "123"
            assert res.expiration is not None
            with db_engine.use_db_session() as session:
                row = session.get(GoogleToken, "u1")
                assert row is not None
                assert row.watch_history_id == "123"


# ---------------------------------------------------------------------------
# History sync via process_notification
# ---------------------------------------------------------------------------


class TestProcessNotification(TestTemplate):
    def _subscribe(self):
        webhook_subscribe(
            WebhookSubscribeInput(user_id="u1", url="https://sub.example/hook")
        )

    def test_sync_enqueues_and_advances_baseline(self):
        client = _fake_client(
            history={"history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]}
        )
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token(history_id="100")
            self._subscribe()
            result = process_notification("a@b.com", "500", "pubsub-1")
            assert result == {"status": "ok", "enqueued": 1}
            with db_engine.use_db_session() as session:
                assert session.query(WebhookDelivery).count() == 1
                row = session.get(GoogleToken, "u1")
                assert row is not None
                assert row.watch_history_id == "500"  # advanced forward
                assert session.get(ProcessedPubsubMessage, "pubsub-1") is not None

    def test_duplicate_pubsub_message_is_ignored(self):
        client = _fake_client(
            history={"history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]}
        )
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token()
            self._subscribe()
            first = process_notification("a@b.com", "500", "dup")
            second = process_notification("a@b.com", "600", "dup")
            assert first["status"] == "ok"
            assert second == {"status": "duplicate"}
            with db_engine.use_db_session() as session:
                assert session.query(WebhookDelivery).count() == 1

    def test_expired_baseline_resets_without_replay(self):
        # On 404 (expired baseline) we reset the baseline forward WITHOUT
        # replaying the backlog, so subscribers aren't spammed with old
        # messages delivered as brand-new events.
        client = _fake_client(history_error=_http_error(404))
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token(history_id="1")
            self._subscribe()
            result = process_notification("a@b.com", "999", "pubsub-x")
            assert result == {"status": "ok", "enqueued": 0}
            with db_engine.use_db_session() as session:
                assert session.query(WebhookDelivery).count() == 0
                row = session.get(GoogleToken, "u1")
                assert row is not None
                assert row.watch_history_id == "999"  # reset forward

    def test_baseline_never_regresses_on_out_of_order_push(self):
        # A notification carrying a LOWER historyId than the stored baseline
        # must not rewind it (which would re-walk + re-deliver old messages).
        client = _fake_client(history={"history": []})
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token(history_id="500")
            self._subscribe()
            result = process_notification("a@b.com", "300", "pubsub-oo")
            assert result["status"] == "ok"
            with db_engine.use_db_session() as session:
                row = session.get(GoogleToken, "u1")
                assert row is not None
                assert row.watch_history_id == "500"  # not rewound to 300

    def test_deleted_message_is_skipped_not_poisoning_the_batch(self):
        # history reports two added messages; one 404s on get (deleted before
        # fetch). The batch must skip it and still enqueue the survivor.
        client = _fake_client(
            history={
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "m_gone"}},
                            {"message": {"id": "m_ok"}},
                        ]
                    }
                ]
            },
        )

        def _get(**kw):
            req = MagicMock()
            if kw["id"] == "m_gone":
                req.execute = MagicMock(side_effect=_http_error(404))
            else:
                req.execute.return_value = {
                    "id": "m_ok",
                    "threadId": "t",
                    "labelIds": ["INBOX"],
                    "snippet": "hi",
                    "payload": {"headers": []},
                }
            return req

        client.users.return_value.messages.return_value.get = MagicMock(
            side_effect=_get
        )
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token(history_id="100")
            self._subscribe()
            result = process_notification("a@b.com", "600", "pubsub-del")
            assert result == {"status": "ok", "enqueued": 1}

    def test_multi_page_history_pagination(self):
        client = _fake_client()
        pages = [
            {
                "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                "nextPageToken": "p2",
            },
            {"history": [{"messagesAdded": [{"message": {"id": "m2"}}]}]},
        ]
        client.users.return_value.history.return_value.list.return_value.execute = (
            MagicMock(side_effect=pages)
        )
        with (
            _patch_db(),
            _plaintext_encryption(),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            _seed_token(history_id="100")
            self._subscribe()
            result = process_notification("a@b.com", "700", "pubsub-pg")
            assert result == {"status": "ok", "enqueued": 2}

    def test_unknown_email_is_acked_without_enqueue(self):
        with _patch_db(), _plaintext_encryption():
            result = process_notification("nobody@x.com", "5", "pubsub-u")
            assert result == {"status": "unknown_user"}


# ---------------------------------------------------------------------------
# Push receiver route (OIDC + envelope)
# ---------------------------------------------------------------------------


def _envelope(email="a@b.com", history_id=123, message_id="pm-1") -> dict:
    data = base64.b64encode(
        json.dumps({"emailAddress": email, "historyId": history_id}).encode()
    ).decode()
    return {"message": {"data": data, "messageId": message_id}}


class TestPushRoute(TestTemplate):
    def test_missing_bearer_is_401(self):
        with patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"):
            client = TestClient(app)
            resp = client.post("/api/v1/google/webhook/gmail", json=_envelope())
            assert resp.status_code == 401

    def test_valid_token_dispatches(self):
        with (
            patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"),
            patch.object(global_config, "GMAIL_PUSH_SA_EMAIL", "sa@proj.iam"),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "sa@proj.iam", "email_verified": True},
            ),
            patch(
                "api_server.routes.google.webhooks.process_notification",
                return_value={"status": "ok", "enqueued": 1},
            ) as proc,
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/webhook/gmail",
                json=_envelope(),
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 200
            assert resp.json()["enqueued"] == 1
            proc.assert_called_once_with("a@b.com", "123", "pm-1")

    def test_wrong_service_account_is_403(self):
        with (
            patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"),
            patch.object(global_config, "GMAIL_PUSH_SA_EMAIL", "sa@proj.iam"),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "attacker@evil.com", "email_verified": True},
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/webhook/gmail",
                json=_envelope(),
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 403

    def test_invalid_oidc_token_is_401(self):
        with (
            patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                side_effect=ValueError("bad token"),
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/webhook/gmail",
                json=_envelope(),
                headers={"Authorization": "Bearer bad"},
            )
            assert resp.status_code == 401

    def test_unverified_email_is_403(self):
        with (
            patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"),
            patch.object(global_config, "GMAIL_PUSH_SA_EMAIL", "sa@proj.iam"),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "sa@proj.iam", "email_verified": False},
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/webhook/gmail",
                json=_envelope(),
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 403

    def test_missing_sa_email_in_prod_is_503(self):
        # Fail closed: without GMAIL_PUSH_SA_EMAIL, any Google-signed token
        # would pass, so outside dev the receiver refuses to run.
        with (
            patch.object(global_config, "GMAIL_PUSH_AUDIENCE", "aud"),
            patch.object(global_config, "GMAIL_PUSH_SA_EMAIL", None),
            patch.object(global_config, "DEV_ENV", "prod"),
            patch(
                "google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "whoever@x.com", "email_verified": True},
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/webhook/gmail",
                json=_envelope(),
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 503

    def test_internal_renew_requires_token(self):
        with patch.object(global_config, "WEBHOOK_RUNNER_TOKEN", "s3cret"):
            client = TestClient(app)
            bad = client.post(
                "/api/v1/google/internal/renew",
                headers={"X-Runner-Token": "wrong"},
            )
            assert bad.status_code == 401

    def test_internal_renew_disabled_without_token(self):
        with patch.object(global_config, "WEBHOOK_RUNNER_TOKEN", None):
            client = TestClient(app)
            resp = client.post("/api/v1/google/internal/renew")
            assert resp.status_code == 503

    def test_internal_renew_success_drives_both_jobs(self):
        with (
            patch.object(global_config, "WEBHOOK_RUNNER_TOKEN", "s3cret"),
            patch(
                "api_server.routes.google.webhooks.renew_due_watches",
                return_value=2,
            ),
            patch(
                "api_server.routes.google.webhooks.drain_due_deliveries",
                return_value={"sent": 3},
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/google/internal/renew",
                headers={"X-Runner-Token": "s3cret"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"renewed": 2, "drained": {"sent": 3}}


class TestRenewDueWatches(TestTemplate):
    def test_selects_only_topic_bound_near_expiry_rows(self):
        client = _fake_client()  # watch() returns a fresh historyId/expiration
        with (
            _patch_db(),
            patch.object(global_config, "GMAIL_PUBSUB_TOPIC", "projects/p/topics/t"),
            patch("services.gmail_watch_svc._get_gmail_client", return_value=client),
        ):
            now = datetime.now(UTC)
            with db_engine.use_db_session() as session:
                # due: no expiration
                session.add(
                    GoogleToken(
                        user_id="due_none",
                        refresh_token_enc=b"x",
                        key_id="plaintext",
                        watch_topic="projects/p/topics/t",
                        watch_expiration=None,
                    )
                )
                # not due: expires far in the future
                session.add(
                    GoogleToken(
                        user_id="fresh",
                        refresh_token_enc=b"x",
                        key_id="plaintext",
                        watch_topic="projects/p/topics/t",
                        watch_expiration=now + timedelta(days=10),
                    )
                )
                # never watched: no topic -> excluded
                session.add(
                    GoogleToken(
                        user_id="no_watch",
                        refresh_token_enc=b"x",
                        key_id="plaintext",
                        watch_topic=None,
                    )
                )
                session.commit()

            assert renew_due_watches() == 1
