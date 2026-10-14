"""Shared setup for the tests of DaySurface's own OAuth server (MCP_AUTH_MODE=self).

Drives the real MCP SDK routes and DaySurface's consent/callback routes against
an in-memory database. Only the two network calls are faked: fetching the
client's metadata document and WorkOS's code exchange.
"""

import base64
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api_server.oauth as oauth_server
from api_server.oauth import cimd, flow
from api_server.routes import well_known
from common import global_config
from db.base import Base
from tests.test_template import TestTemplate

ISSUER = "https://mcp.test.example"
MCP_URL = f"{ISSUER}/mcp"
CLIENT_ID = "https://client.test.example/oauth/metadata.json"
REDIRECT_URI = "https://client.test.example/callback"
USER_ID = "user_01TESTUSER"
WORKOS_SESSION = "session_01TESTSESSION"


def client_document(**overrides) -> bytes:
    doc = {
        "client_id": CLIENT_ID,
        "client_name": "Test Client",
        "redirect_uris": [REDIRECT_URI],
    }
    doc.update(overrides)
    return json.dumps(doc).encode()


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def form_value(page: str, name: str) -> str:
    match = re.search(rf"name='{name}' value='([^']*)'", page)
    assert match, f"no {name} field on the consent page"
    return match.group(1)


def consent_form(page: str, decision: str) -> dict[str, str]:
    return {
        "request": form_value(page, "request"),
        "flow": form_value(page, "flow"),
        "nonce": form_value(page, "nonce"),
        "decision": decision,
    }


def bearer_scope(token: str) -> dict:
    return {"headers": [(b"authorization", f"Bearer {token}".encode())]}


class OAuthServerCase(TestTemplate):
    """Base class: an app with the OAuth routes, a fresh database, fakes."""

    def setup_method(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

        @contextmanager
        def _db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        cimd.clear_cache()
        self._patches = [
            patch("api_server.oauth.provider.use_db_session", _db),
            patch("services.account.sign_out_svc.use_db_session", _db),
            patch.object(global_config, "MCP_AUTH_MODE", "self"),
            patch.object(global_config, "MCP_PUBLIC_URL", MCP_URL),
            patch.object(global_config, "WORKOS_CLIENT_ID", "client_test"),
            patch.object(global_config, "WORKOS_API_KEY", "sk_test"),
            patch.object(
                global_config, "SESSION_SECRET_KEY", "test-secret-" + "x" * 32
            ),
            patch("api_server.oauth.cimd._fetch_document", side_effect=self._fetch),
            patch(
                "api_server.oauth.flow._authenticate_with_workos",
                return_value=(USER_ID, WORKOS_SESSION),
            ),
        ]
        for p in self._patches:
            p.start()
        self.document = client_document()

        app = FastAPI()
        app.include_router(flow.router)
        app.include_router(well_known.router)
        app.router.routes.extend(oauth_server.create_sdk_routes())
        self.http = TestClient(app, base_url=ISSUER, follow_redirects=False)

    def teardown_method(self):
        for p in reversed(self._patches):
            p.stop()
        cimd.clear_cache()

    def _fetch(self, client_id: str) -> bytes:
        assert client_id == CLIENT_ID
        return self.document

    # -- helpers -----------------------------------------------------------

    def _authorize(self, challenge: str, **extra) -> str:
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state",
            "resource": MCP_URL,
            **extra,
        }
        response = self.http.get("/authorize", params=params)
        assert response.status_code == 302, response.text
        return response.headers["location"]

    def _sign_in(self, **extra) -> tuple[str, str]:
        """Run the browser leg; return (our authorization code, PKCE verifier)."""
        verifier, challenge = pkce()
        consent_url = self._authorize(challenge, **extra)
        assert consent_url.startswith(f"{ISSUER}/oauth/consent?")
        page = self.http.get(consent_url)
        assert page.status_code == 200
        workos = self.http.post("/oauth/consent", data=consent_form(page.text, "allow"))
        assert workos.status_code == 303
        assert workos.headers["location"].startswith(flow.WORKOS_AUTHORIZE_URL)
        state = parse_qs(urlsplit(workos.headers["location"]).query)["state"][0]
        back = self.http.get(
            "/oauth/workos/callback", params={"code": "wc", "state": state}
        )
        assert back.status_code == 303
        location = urlsplit(back.headers["location"])
        query = parse_qs(location.query)
        assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
        assert query["state"] == ["client-state"]
        assert query["iss"] == [ISSUER]
        return query["code"][0], verifier

    def _token(self, **form) -> dict:
        response = self.http.post("/token", data={"client_id": CLIENT_ID, **form})
        return {"status": response.status_code, **response.json()}

    def _exchange(self, code: str, verifier: str) -> dict:
        return self._token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            code_verifier=verifier,
            resource=MCP_URL,
        )
