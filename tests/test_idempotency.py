"""Tests for generic Idempotency-Key support on mutating API routes."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services as services_pkg
from api_server.idempotency import (
    _canonical_hash,
    cleanup_expired_idempotency_keys,
    execute_idempotent,
)
from db.base import Base
from db.models.idempotency_keys import IdempotencyRecord
from services import ServiceEntry, discover_services, get_registry, service
from tests.test_template import TestTemplate

# Every registered service that mutates state (create/charge/send/delete/
# modify) and therefore gets Idempotency-Key enforcement on its REST route.
# Deliberately a literal: adding a state-changing service without mutating=True
# (or flagging a pure read) must fail here as an explicit diff to this set.
EXPECTED_MUTATING_SERVICES = frozenset(
    {
        # config
        "config_set",
        # doctor (fixer path only; plain `doctor` stays a pure read)
        "doctor_fix",
        # gmail account + watch
        "gmail_disconnect",
        "gmail_watch_start",
        "gmail_watch_stop",
        # gmail drafts + attachments
        "gmail_compose",
        "gmail_update_draft",
        "gmail_discard_draft",
        "gmail_send",
        "gmail_reply_to_thread",
        "gmail_add_attachment",
        "gmail_remove_attachment",
        # gmail thread label writes (+ curation ledger)
        "gmail_mark_thread_read",
        "gmail_archive_thread",
        "gmail_mark_thread_done",
        "gmail_unmark_thread_done",
        # inbox curation ledger
        "inbox_save_curation",
        # pdf documents
        "pdf_open",
        "pdf_edit",
        "pdf_request_signature",
        "pdf_export",
        # webhooks
        "webhook_subscribe",
        "webhook_unsubscribe",
        "webhook_rotate_secret",
    }
)


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


class _Out(BaseModel):
    value: int


class _Aliased(BaseModel):
    """Field with a serialization alias - exposes replay/first-response drift."""

    model_config = ConfigDict(populate_by_name=True)

    snake_value: int = Field(serialization_alias="camelValue")


def _request(key: str | None) -> Request:
    """Build a minimal Starlette request carrying (or omitting) the header."""
    headers = [(b"idempotency-key", key.encode())] if key is not None else []
    return Request({"type": "http", "headers": headers})


class TestServiceDecorator(TestTemplate):
    def test_mutating_defaults_false(self):
        entry = ServiceEntry(
            name="x",
            description="d",
            input_model=_Out,
            output_model=_Out,
            func=lambda b: b,
        )
        assert entry.mutating is False

    def test_decorator_sets_mutating(self):
        before = len(services_pkg._registry)
        try:

            @service(
                name="__test_mut_svc",
                description="d",
                input_model=_Out,
                output_model=_Out,
                mutating=True,
            )
            def _svc(body):
                return body

            entry = next(
                e for e in services_pkg.get_registry() if e.name == "__test_mut_svc"
            )
            assert entry.mutating is True
        finally:
            # Keep the global registry clean so other tests/transports that
            # iterate it aren't affected by this throwaway service.
            del services_pkg._registry[before:]

    def test_mutating_flags_pinned(self):
        """Pin the exact set of mutating=True services (the durable audit).

        Scoped to entries defined under the real ``services`` package so
        throwaway services registered by other tests can't perturb the set.
        """
        discover_services()
        mutating = {
            e.name
            for e in get_registry()
            if e.mutating and e.func.__module__.startswith("services.")
        }
        assert mutating == EXPECTED_MUTATING_SERVICES


class TestIdempotencyModel(TestTemplate):
    def test_tablename_and_pk(self):
        assert IdempotencyRecord.__tablename__ == "idempotency_keys"
        pk = {c.name for c in IdempotencyRecord.__table__.primary_key}
        assert pk == {"user_id", "route", "idempotency_key"}


class TestExecuteIdempotent(TestTemplate):
    def setup_method(self):
        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patcher = patch("api_server.idempotency.use_db_session", _ctx)
        self._patcher.start()
        # Disable opportunistic cleanup so it can't interfere with assertions.
        self._rand = patch("api_server.idempotency.random.random", return_value=1.0)
        self._rand.start()

    def teardown_method(self):
        self._patcher.stop()
        self._rand.stop()

    def _run(self, key, payload, counter):
        def _compute():
            counter["n"] += 1
            return _Out(value=counter["n"])

        return execute_idempotent(
            request=_request(key),
            user_id="u1",
            route="demo",
            request_payload=payload,
            compute=_compute,
        )

    def test_first_call_executes(self):
        counter = {"n": 0}
        result = self._run("k1", {"a": 1}, counter)
        assert isinstance(result, _Out)
        assert result.value == 1
        assert counter["n"] == 1

    def test_replay_does_not_recompute(self):
        counter = {"n": 0}
        self._run("k1", {"a": 1}, counter)
        replay = self._run("k1", {"a": 1}, counter)
        assert counter["n"] == 1  # compute ran only once
        # Replays come back as a raw JSON response carrying the cached body.
        assert replay.status_code == 200
        assert b'"value":1' in replay.body

    def test_different_key_executes_again(self):
        counter = {"n": 0}
        self._run("k1", {"a": 1}, counter)
        self._run("k2", {"a": 1}, counter)
        assert counter["n"] == 2

    def test_same_key_different_payload_conflicts(self):
        counter = {"n": 0}
        self._run("k1", {"a": 1}, counter)
        with pytest.raises(HTTPException) as exc:
            self._run("k1", {"a": 2}, counter)
        assert exc.value.status_code == 422
        assert counter["n"] == 1

    def test_missing_key_rejected(self):
        counter = {"n": 0}
        with pytest.raises(HTTPException) as exc:
            self._run(None, {"a": 1}, counter)
        assert exc.value.status_code == 422
        assert counter["n"] == 0

    def test_client_error_releases_claim(self):
        # A 4xx client error (e.g. quota 402) is raised before the side effect
        # commits, so the claim is released and a same-key retry runs again.
        def _compute_fail():
            raise HTTPException(status_code=402, detail="quota")

        with pytest.raises(HTTPException) as exc:
            execute_idempotent(
                request=_request("k1"),
                user_id="u1",
                route="demo",
                request_payload={"a": 1},
                compute=_compute_fail,
            )
        assert exc.value.status_code == 402

        # Claim was released: a retry with the same key runs compute again.
        counter = {"n": 0}
        result = self._run("k1", {"a": 1}, counter)
        assert result.value == 1
        assert counter["n"] == 1

    def test_ambiguous_failure_keeps_claim(self):
        # A 5xx / ambiguous failure may have committed the side effect remotely,
        # so the claim is kept: a same-key retry returns 409 and does NOT
        # re-execute compute.
        def _compute_fail():
            raise HTTPException(status_code=503, detail="boom")

        with pytest.raises(HTTPException) as exc:
            execute_idempotent(
                request=_request("k1"),
                user_id="u1",
                route="demo",
                request_payload={"a": 1},
                compute=_compute_fail,
            )
        assert exc.value.status_code == 503

        counter = {"n": 0}
        with pytest.raises(HTTPException) as retry:
            self._run("k1", {"a": 1}, counter)
        assert retry.value.status_code == 409
        assert counter["n"] == 0

    def test_in_flight_returns_409(self):
        # Simulate a claimed-but-not-completed row from a concurrent request.
        with self.SessionLocal() as session:
            session.add(
                IdempotencyRecord(
                    user_id="u1",
                    route="demo",
                    idempotency_key="k1",
                    request_hash=_hash({"a": 1}),
                )
            )
            session.commit()

        counter = {"n": 0}
        with pytest.raises(HTTPException) as exc:
            self._run("k1", {"a": 1}, counter)
        assert exc.value.status_code == 409
        assert counter["n"] == 0


class TestCleanup(TestTemplate):
    def test_cleanup_removes_expired(self):
        engine = _make_engine()
        session_local = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = session_local()
            try:
                yield session
            finally:
                session.close()

        with session_local() as session:
            session.add(
                IdempotencyRecord(
                    user_id="u1",
                    route="demo",
                    idempotency_key="old",
                    request_hash="h",
                    created_at=datetime.now(UTC) - timedelta(days=3),
                )
            )
            session.add(
                IdempotencyRecord(
                    user_id="u1",
                    route="demo",
                    idempotency_key="fresh",
                    request_hash="h",
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()

        with patch("api_server.idempotency.use_db_session", _ctx):
            removed = cleanup_expired_idempotency_keys()

        assert removed == 1
        with session_local() as session:
            remaining = session.query(IdempotencyRecord).all()
            assert {r.idempotency_key for r in remaining} == {"fresh"}


class TestIdempotentRouteOverHTTP(TestTemplate):
    """End-to-end over a real ASGI app to exercise header + replay wiring."""

    def setup_method(self):
        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.counter = {"n": 0}

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patcher = patch("api_server.idempotency.use_db_session", _ctx)
        self._patcher.start()
        self._rand = patch("api_server.idempotency.random.random", return_value=1.0)
        self._rand.start()

        app = FastAPI()
        counter = self.counter

        @app.post("/charge", response_model=_Out)
        def _charge(body: _Out, request: Request):
            def _compute():
                counter["n"] += 1
                return _Out(value=counter["n"])

            return execute_idempotent(
                request=request,
                user_id="u1",
                route="charge",
                request_payload=body.model_dump(mode="json"),
                compute=_compute,
            )

        @app.post("/aliased", response_model=_Aliased)
        def _aliased(body: _Aliased, request: Request):
            def _compute():
                counter["n"] += 1
                return _Aliased(snake_value=counter["n"])

            return execute_idempotent(
                request=request,
                user_id="u1",
                route="aliased",
                request_payload=body.model_dump(mode="json"),
                compute=_compute,
            )

        self.client = TestClient(app)

    def teardown_method(self):
        self._patcher.stop()
        self._rand.stop()

    def test_missing_header_422(self):
        resp = self.client.post("/charge", json={"value": 0})
        assert resp.status_code == 422

    def test_replay_returns_same_body(self):
        first = self.client.post(
            "/charge", json={"value": 0}, headers={"Idempotency-Key": "abc"}
        )
        second = self.client.post(
            "/charge", json={"value": 0}, headers={"Idempotency-Key": "abc"}
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert self.counter["n"] == 1

    def test_replay_preserves_field_aliases(self):
        # FastAPI serializes response_model with by_alias=True; the cached
        # replay must use the same aliased keys, not the raw field names.
        first = self.client.post(
            "/aliased", json={"snake_value": 0}, headers={"Idempotency-Key": "al"}
        )
        second = self.client.post(
            "/aliased", json={"snake_value": 0}, headers={"Idempotency-Key": "al"}
        )
        assert first.status_code == 200
        assert "camelValue" in first.json()
        assert first.json() == second.json()
        assert self.counter["n"] == 1


def _hash(payload: dict) -> str:
    return _canonical_hash(payload)
