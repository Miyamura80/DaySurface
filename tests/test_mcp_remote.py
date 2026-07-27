"""Tests for the streamable-HTTP /mcp endpoint mounted on the FastAPI app.

Covers the auth boundary (401 without creds, 200 with a valid API key),
scope enforcement, daily quota enforcement, and a smoke check that the
MCP `initialize` handshake completes end-to-end.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth.api_key_auth import create_api_key
from api_server.auth.unified_auth import AuthenticatedUser
from api_server.server import app
from db import engine as db_engine
from db.base import Base
from mcp_server._tool_factory import _check_quota, _check_scopes
from src.utils.current_user import reset_current_user, set_current_user
from tests.test_template import TestTemplate


@contextmanager
def _patch_db():
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


def _read_sse_first_message(response) -> dict:
    """Parse the first ``data:`` line from an MCP SSE response."""
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("no SSE data frame in response")


class TestMCPRemote(TestTemplate):
    def test_mcp_requires_auth(self):
        # No `with`: the mcp_auth middleware short-circuits before the MCP sub-app
        # is reached, so we don't need to enter the lifespan / session manager.
        client = TestClient(app)
        resp = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": "127.0.0.1:8080",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32001

    @patch("api_server.middleware.mcp_auth.global_config")
    def test_mcp_initialize_with_api_key(self, mock_config):
        # No WorkOS -> middleware falls through to API-key path.
        mock_config.WORKOS_CLIENT_ID = None
        with _patch_db() as session_factory:
            with session_factory() as s:
                raw_key, _ = create_api_key(s, user_id="u-mcp-remote-test")

            with TestClient(app) as client:
                resp = client.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Host": "127.0.0.1:8080",
                        "X-API-KEY": raw_key,
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"},
                        },
                    },
                )
            assert resp.status_code == 200, resp.text
            msg = _read_sse_first_message(resp)
            assert msg["jsonrpc"] == "2.0"
            assert msg["result"]["serverInfo"]["name"] == "mymcp"

    def test_health_endpoint_unaffected_by_mcp_auth(self):
        # Plain TestClient (no lifespan) -- /health doesn't need the MCP session
        # manager and entering the lifespan would clobber other tests' use of it.
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200


class TestMCPAuthGuards(TestTemplate):
    """Verify that MCP tool calls enforce scopes and daily quota."""

    def test_no_user_skips_scope_check(self):
        _check_scopes()

    def test_no_user_skips_quota_check(self):
        _check_quota()

    def test_wildcard_scopes_pass(self):
        user = AuthenticatedUser(user_id="u-admin", auth_method="api_key", scopes=["*"])
        token = set_current_user(user)
        try:
            _check_scopes()
        finally:
            reset_current_user(token)

    def test_read_only_scopes_blocked(self):
        user = AuthenticatedUser(
            user_id="u-readonly", auth_method="api_key", scopes=["services:read"]
        )
        token = set_current_user(user)
        try:
            with pytest.raises(PermissionError, match="services:execute"):
                _check_scopes()
        finally:
            reset_current_user(token)

    def test_quota_exhausted_raises(self):
        user = AuthenticatedUser(
            user_id="u-quota",
            auth_method="api_key",
            scopes=["services:execute"],
        )
        token = set_current_user(user)
        try:
            with patch(
                "api_server.billing.limits.ensure_daily_limit",
                side_effect=HTTPException(status_code=402, detail="quota exceeded"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    _check_quota()
                assert exc_info.value.status_code == 402
        finally:
            reset_current_user(token)
