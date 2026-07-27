"""Tests for the Web Bot Auth key directory.

Served at /.well-known/http-message-signatures-directory
(draft-meunier-http-message-signatures-directory): the agent's Ed25519 public
signing key(s) as a JWK Set. Like the other discovery documents it is
unauthenticated and must be readable cross-origin. The directory is omitted
(404) until a signing key is configured.
"""

import base64
import hashlib
import json
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from api_server.routes import well_known
from api_server.server import app

DIRECTORY_PATH = "/.well-known/http-message-signatures-directory"


def _seed_b64() -> str:
    """A fresh base64url (unpadded) 32-byte Ed25519 seed, as the secret expects."""
    raw = Ed25519PrivateKey.generate().private_bytes_raw()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class TestWebBotAuthDirectory:
    def _client(self) -> TestClient:
        # No lifespan needed: this route never reaches the MCP sub-app.
        return TestClient(app)

    def setup_method(self):
        # Key material is memoized per seed; isolate each test from the others.
        well_known._signing_key_jwk.cache_clear()

    def test_404_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", None)
        assert self._client().get(DIRECTORY_PATH).status_code == 404

    def test_404_when_whitespace_only(self, monkeypatch):
        # A blank-but-spaced secret is "unconfigured" (404), not a bad seed (500).
        monkeypatch.setattr(well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", "   ")
        assert self._client().get(DIRECTORY_PATH).status_code == 404

    def test_publishes_ed25519_jwk_set(self, monkeypatch):
        seed = _seed_b64()
        monkeypatch.setattr(well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", seed)

        resp = self._client().get(DIRECTORY_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "application/http-message-signatures-directory+json"
        )

        keys = resp.json()["keys"]
        assert len(keys) == 1
        jwk = keys[0]
        assert jwk["kty"] == "OKP"
        assert jwk["crv"] == "Ed25519"
        assert jwk["use"] == "sig"
        assert jwk["x"]
        assert jwk["kid"]

        # nbf/exp are numeric and bracket "now".
        now = int(time.time())
        assert isinstance(jwk["nbf"], int) and isinstance(jwk["exp"], int)
        assert jwk["nbf"] <= now < jwk["exp"]

    def test_x_matches_configured_key_and_kid_is_thumbprint(self, monkeypatch):
        # Derive the expected public key + RFC 7638 thumbprint independently.
        raw = Ed25519PrivateKey.generate().private_bytes_raw()
        seed = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        monkeypatch.setattr(well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", seed)

        pub = (
            Ed25519PrivateKey.from_private_bytes(raw)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        expected_x = base64.urlsafe_b64encode(pub).rstrip(b"=").decode("ascii")
        canonical = json.dumps(
            {"crv": "Ed25519", "kty": "OKP", "x": expected_x},
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_kid = (
            base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest())
            .rstrip(b"=")
            .decode("ascii")
        )

        jwk = self._client().get(DIRECTORY_PATH).json()["keys"][0]
        assert jwk["x"] == expected_x
        assert jwk["kid"] == expected_kid

    def test_500_on_malformed_seed(self, monkeypatch):
        # A configured-but-invalid seed is a deployment error, surfaced as 500.
        monkeypatch.setattr(
            well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", "not-a-valid-seed"
        )
        assert self._client().get(DIRECTORY_PATH).status_code == 500

    def test_is_cors_readable(self, monkeypatch):
        monkeypatch.setattr(
            well_known.global_config, "WEB_BOT_AUTH_PRIVATE_KEY", _seed_b64()
        )
        resp = self._client().get(DIRECTORY_PATH)
        assert resp.headers.get("access-control-allow-origin") == "*"
