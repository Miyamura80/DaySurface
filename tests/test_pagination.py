"""Cursor-pagination codec unit tests + list endpoint integration tests."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.pagination import decode_cursor, encode_cursor
from api_server.server import app
from db.base import Base
from db.engine import get_db_session
from db.models.api_keys import APIKey
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)


def _override_auth():
    return AuthenticatedUser(user_id="page-user", email="p@p.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestCursorCodec(TestTemplate):
    def test_round_trip(self):
        when = datetime(2025, 1, 15, 10, 30, tzinfo=UTC)
        token = encode_cursor(when, 42)
        decoded_when, decoded_id = decode_cursor(token)
        assert decoded_when == when
        assert decoded_id == 42

    def test_token_is_url_safe_and_unpadded(self):
        token = encode_cursor(datetime(2025, 1, 1, tzinfo=UTC), 1)
        assert "=" not in token
        assert "+" not in token
        assert "/" not in token

    def test_bad_cursor_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            decode_cursor("not-a-real-cursor!!!")
        assert exc.value.status_code == 400


class TestListKeysPagination(TestTemplate):
    def setup_method(self):
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        self.client = TestClient(app)
        self._seed(5)

    def teardown_method(self):
        session = _SessionLocal()
        try:
            session.query(APIKey).delete()
            session.commit()
        finally:
            session.close()
        app.dependency_overrides.clear()

    def _seed(self, n: int):
        session = _SessionLocal()
        try:
            base = datetime(2025, 1, 1, tzinfo=UTC)
            for i in range(n):
                session.add(
                    APIKey(
                        user_id="page-user",
                        key_hash=f"hash-{i}",
                        key_prefix=f"sk_live_{i}",
                        name=f"key-{i}",
                        created_at=base + timedelta(minutes=i),
                    )
                )
            session.commit()
        finally:
            session.close()

    def test_envelope_shape(self):
        resp = self.client.get("/api/v1/auth/api-keys?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"items", "next_cursor", "has_more"}
        assert len(data["items"]) == 2
        assert data["has_more"] is True
        assert data["next_cursor"]

    def test_walk_all_pages_without_overlap(self):
        seen: list[int] = []
        cursor = None
        for _ in range(10):  # generous upper bound to avoid an infinite loop
            url = "/api/v1/auth/api-keys?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            data = self.client.get(url).json()
            seen.extend(item["id"] for item in data["items"])
            cursor = data["next_cursor"]
            if not data["has_more"]:
                break
        assert len(seen) == 5
        assert len(set(seen)) == 5  # no duplicates across pages

    def test_last_page_has_no_cursor(self):
        data = self.client.get("/api/v1/auth/api-keys?limit=100").json()
        assert data["has_more"] is False
        assert data["next_cursor"] is None
        assert len(data["items"]) == 5

    def test_bad_cursor_returns_400(self):
        resp = self.client.get("/api/v1/auth/api-keys?cursor=garbage!!!")
        assert resp.status_code == 400

    def test_limit_is_bounded(self):
        # Over the max (100) -> 422 validation error, not an unbounded scan.
        assert self.client.get("/api/v1/auth/api-keys?limit=1000").status_code == 422
        assert self.client.get("/api/v1/auth/api-keys?limit=0").status_code == 422
