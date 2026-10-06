"""Fakes for the Gmail service tests: an in-memory DB, a token row, a
chainable MagicMock Gmail client, and Gmail API payload builders."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken


@contextmanager
def patch_db():
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


def seed_token(factory, user_id: str = "alice") -> None:
    s = factory()
    s.add(
        GoogleToken(
            user_id=user_id,
            email=f"{user_id}@example.com",
            refresh_token_enc=b"RT",
            key_id="plaintext",
            scopes=["openid", "email"],
        )
    )
    s.commit()
    s.close()


def b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def header_list(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in d.items()]


def plain_message(
    *,
    message_id: str = "m-1",
    thread_id: str = "t-1",
    headers: dict[str, str] | None = None,
    body: str = "hello world",
    snippet: str = "hello world",
    internal_date_ms: int | None = None,
    label_ids: list[str] | None = None,
) -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "internalDate": str(internal_date_ms) if internal_date_ms else "1700000000000",
        "labelIds": label_ids or [],
        "payload": {
            "mimeType": "text/plain",
            "headers": header_list(
                headers or {"From": "a@x", "To": "b@y", "Subject": "hi"}
            ),
            "body": {"data": b64url(body), "size": len(body)},
        },
    }


def draft_resource(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    body: str = "hello world",
    thread_id: str = "t-1",
) -> dict:
    return {
        "id": draft_id,
        "message": plain_message(
            message_id=f"m-{draft_id}",
            thread_id=thread_id,
            headers={"To": to, "Subject": subject},
            body=body,
            snippet=body[:50],
        ),
    }


def make_mock_service() -> MagicMock:
    """A MagicMock that supports the chained ``.users().drafts().get().execute()`` style."""
    mock = MagicMock()
    mock.users().labels().list().execute.return_value = {"labels": []}
    mock.users().drafts().list().execute.return_value = {"drafts": []}
    return mock


def patch_client(mock_svc: object):
    # Patch every import site so each service module picks it up.
    return [
        patch("services.gmail_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_drafts_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_reply_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_messages_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_curate_svc._get_gmail_client", return_value=mock_svc),
        patch(
            "services.gmail_attachments_svc._get_gmail_client", return_value=mock_svc
        ),
    ]


def start_patches(patches):
    return [p.start() for p in patches]


def stop_patches(patches):
    for p in patches:
        p.stop()
