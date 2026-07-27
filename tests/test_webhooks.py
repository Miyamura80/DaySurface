"""Tests for outbound webhook services + delivery outbox (fast tier, no Node).

Covers subscription CRUD, event fan-out (enqueue_event), and the delivery
runner: signature correctness on success, exponential-backoff retry on
failure, and give-up at WEBHOOK_MAX_ATTEMPTS. HTTP is stubbed with an
httpx.MockTransport so no network is touched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config
from common.token_encryption import PlaintextEncryption
from db import engine as db_engine
from db.base import Base
from db.models.webhooks import WebhookDelivery
from models.webhooks import (
    WebhookListInput,
    WebhookRotateSecretInput,
    WebhookSubscribeInput,
    WebhookUnsubscribeInput,
)
from services.webhook_delivery_svc import _backoff_seconds, drain_due_deliveries
from services.webhooks_svc import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    enqueue_event,
    webhook_list,
    webhook_rotate_secret,
    webhook_subscribe,
    webhook_unsubscribe,
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
    """Force PlaintextEncryption so no Fernet key is needed under test."""
    with patch(
        "services.webhooks_svc.require_encryption",
        return_value=PlaintextEncryption(),
    ):
        yield


@contextmanager
def _mock_http(handler):
    """Patch the delivery module's httpx.Client to use a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client  # capture before patching to avoid recursion

    def factory(*_args, **_kwargs):
        return real_client(transport=transport)

    with patch("services.webhook_delivery_svc.httpx.Client", factory):
        yield


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


class TestWebhookSubscriptions(TestTemplate):
    def test_subscribe_returns_secret_and_persists(self):
        with _patch_db(), _plaintext_encryption():
            res = webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url="https://example.com/hook")
            )
            assert res.secret.startswith("whsec_")
            assert res.active is True

            listed = webhook_list(WebhookListInput(user_id="u1"))
            assert len(listed.subscriptions) == 1
            view = listed.subscriptions[0]
            assert view.id == res.id
            assert view.url == "https://example.com/hook"
            # The secret must never be echoed back by list.
            assert not hasattr(view, "secret")

    def test_subscribe_rejects_non_http_url(self):
        with _patch_db(), _plaintext_encryption():
            try:
                webhook_subscribe(WebhookSubscribeInput(user_id="u1", url="ftp://nope"))
                raise AssertionError("expected ValueError")
            except ValueError as exc:
                assert "http" in str(exc)

    def test_unsubscribe_deactivates_then_is_idempotent(self):
        with _patch_db(), _plaintext_encryption():
            res = webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url="https://e.com/h")
            )
            first = webhook_unsubscribe(
                WebhookUnsubscribeInput(user_id="u1", subscription_id=res.id)
            )
            assert first.unsubscribed is True
            second = webhook_unsubscribe(
                WebhookUnsubscribeInput(user_id="u1", subscription_id=res.id)
            )
            assert second.unsubscribed is False

    def test_rotate_secret_changes_secret(self):
        with _patch_db(), _plaintext_encryption():
            res = webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url="https://e.com/h")
            )
            rotated = webhook_rotate_secret(
                WebhookRotateSecretInput(user_id="u1", subscription_id=res.id)
            )
            assert rotated.secret != res.secret
            assert rotated.secret.startswith("whsec_")

    def test_subscribe_rejects_private_and_metadata_ips(self):
        # SSRF guard: literal private / link-local addresses are blocked
        # regardless of DEV_ENV so a tenant cannot reach cloud metadata.
        with _patch_db(), _plaintext_encryption():
            for bad in (
                "https://169.254.169.254/latest/meta-data/",  # link-local
                "https://10.0.0.5/internal",  # private
                "https://192.168.1.1/admin",  # private
            ):
                with pytest.raises(ValueError, match="private|reserved|loopback"):
                    webhook_subscribe(WebhookSubscribeInput(user_id="u1", url=bad))

    def test_subscribe_rejects_plaintext_http_to_public_host(self):
        with (
            _patch_db(),
            _plaintext_encryption(),
            pytest.raises(ValueError, match="https"),
        ):
            webhook_subscribe(
                WebhookSubscribeInput(user_id="u1", url="http://example.com/h")
            )


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


class TestEnqueueEvent(TestTemplate):
    def _make_sub(self, user_id, event_types=None):
        return webhook_subscribe(
            WebhookSubscribeInput(
                user_id=user_id,
                url="https://e.com/h",
                event_types=event_types,
            )
        )

    def test_fans_out_to_matching_active_subs(self):
        with _patch_db(), _plaintext_encryption():
            self._make_sub("u1")
            with db_engine.use_db_session() as session:
                event_id = enqueue_event(
                    session,
                    user_id="u1",
                    event_type="gmail.message.new",
                    payload={"snippet": "hi"},
                )
                session.commit()
            assert event_id is not None
            with db_engine.use_db_session() as session:
                deliveries = session.query(WebhookDelivery).all()
                assert len(deliveries) == 1
                assert deliveries[0].status == "pending"

    def test_no_delivery_when_event_type_filtered_out(self):
        with _patch_db(), _plaintext_encryption():
            self._make_sub("u1", event_types=["other.event"])
            with db_engine.use_db_session() as session:
                event_id = enqueue_event(
                    session,
                    user_id="u1",
                    event_type="gmail.message.new",
                    payload={},
                )
                session.commit()
            assert event_id is None
            with db_engine.use_db_session() as session:
                assert session.query(WebhookDelivery).count() == 0

    def test_fans_out_to_all_matching_subs_only(self):
        with _patch_db(), _plaintext_encryption():
            self._make_sub("u1")  # matches all (no filter)
            self._make_sub("u1", event_types=["gmail.message.new"])  # matches
            self._make_sub("u1", event_types=["other.event"])  # no match
            inactive = self._make_sub("u1")
            webhook_unsubscribe(
                WebhookUnsubscribeInput(user_id="u1", subscription_id=inactive.id)
            )
            with db_engine.use_db_session() as session:
                event_id = enqueue_event(
                    session,
                    user_id="u1",
                    event_type="gmail.message.new",
                    payload={"snippet": "x"},
                )
                session.commit()
            with db_engine.use_db_session() as session:
                deliveries = session.query(WebhookDelivery).all()
                # Exactly the two active matching subs; all point at one event.
                assert len(deliveries) == 2
                assert {d.event_id for d in deliveries} == {event_id}


# ---------------------------------------------------------------------------
# Delivery runner
# ---------------------------------------------------------------------------


class TestDelivery(TestTemplate):
    def _seed(self, event_types=None):
        res = webhook_subscribe(
            WebhookSubscribeInput(
                user_id="u1", url="https://sub.example/hook", event_types=event_types
            )
        )
        with db_engine.use_db_session() as session:
            enqueue_event(
                session,
                user_id="u1",
                event_type="gmail.message.new",
                payload={"snippet": "hello"},
            )
            session.commit()
        return res

    def test_successful_delivery_marks_succeeded_with_valid_signature(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            captured["body"] = request.content
            return httpx.Response(200)

        with _patch_db(), _plaintext_encryption():
            res = self._seed()
            with _mock_http(handler):
                counts = drain_due_deliveries()

            assert counts["sent"] == 1

            # Independently recompute the HMAC a subscriber would verify -
            # using the stdlib directly, not sign_payload, so a bug in the
            # signer can't make this pass circularly.
            ts = int(captured["headers"][TIMESTAMP_HEADER])
            body = captured["body"]
            mac = hmac.new(
                res.secret.encode("utf-8"),
                f"{ts}.".encode() + body,
                hashlib.sha256,
            ).hexdigest()
            assert captured["headers"][SIGNATURE_HEADER] == f"sha256={mac}"

            # The delivered body carries the event envelope.
            decoded = json.loads(body)
            assert set(decoded) == {"id", "type", "created_at", "data"}
            assert decoded["type"] == "gmail.message.new"
            assert decoded["data"] == {"snippet": "hello"}

            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "succeeded"
                assert d.attempts == 1

    def test_failure_schedules_retry_with_backoff(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with _patch_db(), _plaintext_encryption():
            self._seed()
            before = datetime.now(UTC)
            with _mock_http(handler):
                counts = drain_due_deliveries()

            assert counts["retry"] == 1
            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "pending"
                assert d.attempts == 1
                assert d.last_error is not None
                # next attempt pushed into the future by the backoff schedule.
                # SQLite returns naive datetimes; normalize before comparing.
                next_at = d.next_attempt_at
                if next_at.tzinfo is None:
                    next_at = next_at.replace(tzinfo=UTC)
                assert next_at > before

    def test_gives_up_at_max_attempts(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        with (
            _patch_db(),
            _plaintext_encryption(),
            patch.object(global_config, "WEBHOOK_MAX_ATTEMPTS", 1),
        ):
            self._seed()
            with _mock_http(handler):
                counts = drain_due_deliveries()

            assert counts["failed"] == 1
            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "failed"
                assert d.attempts == 1

    def test_inactive_subscription_is_dropped_without_attempt(self):
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200)

        with _patch_db(), _plaintext_encryption():
            res = self._seed()
            # Deactivate the sub after the delivery was enqueued.
            webhook_unsubscribe(
                WebhookUnsubscribeInput(user_id="u1", subscription_id=res.id)
            )
            with _mock_http(handler):
                counts = drain_due_deliveries()

            assert counts["dropped"] == 1
            assert calls["n"] == 0  # never POSTed
            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "failed"
                assert d.attempts == 0
                assert "inactive" in (d.last_error or "")

    def test_non_http_error_is_isolated_not_raised(self):
        # A secret that cannot be decrypted must not wedge the outbox: the row
        # follows the normal retry path instead of propagating.
        def boom(_ciphertext):
            raise ValueError("undecryptable secret")

        with _patch_db(), _plaintext_encryption():
            self._seed()
            with patch(
                "services.webhook_delivery_svc.decrypt_secret", side_effect=boom
            ):
                counts = drain_due_deliveries()

            assert counts["retry"] == 1
            with db_engine.use_db_session() as session:
                d = session.query(WebhookDelivery).one()
                assert d.status == "pending"
                assert d.attempts == 1
                assert "undecryptable" in (d.last_error or "")


class TestBackoffSchedule(TestTemplate):
    def test_backoff_doubles_then_caps(self):
        assert _backoff_seconds(1) == 30
        assert _backoff_seconds(2) == 60
        assert _backoff_seconds(3) == 120
        assert _backoff_seconds(4) == 240
        # Saturates at the 1-hour cap.
        assert _backoff_seconds(20) == 3600
