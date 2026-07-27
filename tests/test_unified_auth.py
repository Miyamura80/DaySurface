"""Tests for the unified auth fallback chain: JWT -> API key -> 401."""

import json
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.auth.api_key_auth import create_api_key
from db.base import Base
from db.engine import get_db_session
from tests.test_template import TestTemplate


def _setup_app():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    app = FastAPI()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db

    @app.get("/test-auth")
    def protected(user: AuthenticatedUser = Depends(get_authenticated_user)):
        return {"user_id": user.user_id, "method": user.auth_method}

    return app, session_factory


class TestUnifiedAuth(TestTemplate):
    @patch("api_server.auth.workos_auth.global_config")
    def test_jwt_auth_succeeds(self, mock_config):
        mock_config.WORKOS_CLIENT_ID = "test-client"
        mock_config.DEV_ENV = "dev"
        app, _sl = _setup_app()
        client = TestClient(app)
        token = json.dumps({"sub": "jwt-user", "email": "j@t.com"})
        resp = client.get("/test-auth", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "jwt-user"
        assert resp.json()["method"] == "jwt"

    @patch("api_server.auth.workos_auth.global_config")
    def test_api_key_fallback(self, mock_config):
        mock_config.WORKOS_CLIENT_ID = None  # JWT disabled
        app, sl = _setup_app()
        session = sl()
        raw_key, _row = create_api_key(session, user_id="key-user")
        session.close()

        client = TestClient(app)
        resp = client.get("/test-auth", headers={"X-API-KEY": raw_key})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "key-user"
        assert resp.json()["method"] == "api_key"

    @patch("api_server.auth.workos_auth.global_config")
    def test_scopes_propagate_through_api_key(self, mock_config):
        """Scopes set on an API key should be available in AuthenticatedUser."""
        mock_config.WORKOS_CLIENT_ID = None
        app, sl = _setup_app()

        @app.get("/test-scopes")
        def scoped(user: AuthenticatedUser = Depends(get_authenticated_user)):
            return {"scopes": user.scopes}

        session = sl()
        raw_key, _row = create_api_key(
            session, user_id="scoped-user", scopes=["services:read"]
        )
        session.close()

        client = TestClient(app)
        resp = client.get("/test-scopes", headers={"X-API-KEY": raw_key})
        assert resp.status_code == 200
        assert resp.json()["scopes"] == ["services:read"]

    @patch("api_server.auth.unified_auth.global_config")
    @patch("api_server.auth.workos_auth.global_config")
    def test_invalid_bearer_fails_fast(self, mock_workos_config, mock_unified_config):
        """An invalid Bearer token should 401 when WorkOS is configured."""
        mock_workos_config.WORKOS_CLIENT_ID = "test-client"
        mock_workos_config.DEV_ENV = "prod"
        mock_unified_config.WORKOS_CLIENT_ID = "test-client"
        app, sl = _setup_app()
        session = sl()
        raw_key, _row = create_api_key(session, user_id="key-user")
        session.close()

        client = TestClient(app)
        resp = client.get(
            "/test-auth",
            headers={"Authorization": "Bearer bad-token", "X-API-KEY": raw_key},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid Bearer token"

    @patch("api_server.auth.unified_auth.global_config")
    @patch("api_server.auth.workos_auth.global_config")
    def test_bearer_falls_through_when_workos_unconfigured(
        self, mock_workos_config, mock_unified_config
    ):
        """Bearer header should fall through to API key when WorkOS is not configured."""
        mock_workos_config.WORKOS_CLIENT_ID = None
        mock_unified_config.WORKOS_CLIENT_ID = None
        app, sl = _setup_app()
        session = sl()
        raw_key, _row = create_api_key(session, user_id="key-user")
        session.close()

        client = TestClient(app)
        resp = client.get(
            "/test-auth",
            headers={"Authorization": "Bearer irrelevant", "X-API-KEY": raw_key},
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "key-user"
        assert resp.json()["method"] == "api_key"

    @patch("api_server.auth.workos_auth.global_config")
    def test_no_credentials_returns_401(self, mock_config):
        mock_config.WORKOS_CLIENT_ID = None
        app, _sl = _setup_app()
        client = TestClient(app)
        resp = client.get("/test-auth")
        assert resp.status_code == 401
