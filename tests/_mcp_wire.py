"""Shared streamable-HTTP wire helpers for the /mcp E2E tests.

A minimal handshake-era JSON-RPC client (:class:`McpSession`) and the fixtures
that stand up the FastAPI app over an in-memory database with a fresh API key.
Used by ``tests/test_mcp_e2e.py``, ``tests/test_mcp_protocol_eras.py`` and
``tests/test_gmail_retry_wire.py``.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth.api_key_auth import create_api_key
from api_server.server import app
from db import engine as db_engine
from db.base import Base

PROTOCOL_VERSION = "2025-03-26"


@contextmanager
def patch_db():
    """Wire an in-memory SQLite into db.engine for the duration of the block."""
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


class McpSession:
    """Minimal JSON-RPC client speaking streamable HTTP against a TestClient."""

    def __init__(
        self,
        client: TestClient,
        api_key: str,
        protocol_version: str = PROTOCOL_VERSION,
    ):
        self._client = client
        self._api_key = api_key
        self._session_id: str | None = None
        self._next_id = 0
        self._requested_version = protocol_version
        self._protocol_version = protocol_version
        self._negotiated = False

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": "127.0.0.1:8080",
            "X-API-KEY": self._api_key,
        }
        # Post-initialize requests must carry the *negotiated* version, not the
        # one the client originally requested. ``initialize`` itself carries
        # none: a version header outside the handshake set would route the
        # POST to the 2026-07-28 stateless entry instead.
        if self._negotiated:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def request(self, method: str, params: dict | None = None) -> dict:
        """POST a JSON-RPC request; return the `result` member of the response."""
        self._next_id += 1
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        if self._session_id is None:
            self._session_id = resp.headers.get("mcp-session-id")
        msg = read_sse_first_message(resp)
        assert "error" not in msg, f"{method} returned error: {msg.get('error')}"
        return msg["result"]

    def request_error(self, method: str, params: dict | None = None) -> dict:
        """POST a JSON-RPC request expected to fail; return the `error` member."""
        self._next_id += 1
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        msg = read_sse_first_message(resp)
        assert "error" in msg, f"{method} unexpectedly succeeded: {msg}"
        return msg["error"]

    def notify(self, method: str) -> None:
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": method},
        )
        assert resp.status_code == 202, f"{method}: {resp.status_code} {resp.text}"

    def handshake(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": self._requested_version,
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "0"},
            },
        )
        self._protocol_version = result["protocolVersion"]
        self._negotiated = True
        self.notify("notifications/initialized")
        return result


@contextmanager
def wire_client(user_id: str):
    """Yield ``(TestClient, api_key)`` backed by a fresh DB and API key."""
    # No WorkOS: the middleware falls through to the API-key path.
    with (
        patch(
            "api_server.middleware.mcp_auth.global_config",
            WORKOS_CLIENT_ID=None,
            WORKOS_AUTHKIT_DOMAIN=None,
        ),
        patch_db() as session_factory,
    ):
        with session_factory() as s:
            raw_key, _ = create_api_key(s, user_id=user_id, scopes=["*"])

        with TestClient(app) as client:
            yield client, raw_key


@contextmanager
def wire_session(user_id: str, protocol_version: str = PROTOCOL_VERSION):
    """Yield a handshaken (handshake-era) MCP session."""
    with wire_client(user_id) as (client, raw_key):
        session = McpSession(client, raw_key, protocol_version)
        session.handshake()
        yield session
