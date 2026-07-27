"""Tests for WorkOS JWT verification."""

import json
import time
from unittest.mock import patch

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa

from api_server.auth.workos_auth import verify_workos_token
from tests.test_template import TestTemplate


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


class TestWorkOSAuth(TestTemplate):
    @patch("api_server.auth.workos_auth.global_config")
    def test_test_mode_bypass(self, mock_config):
        """JSON test-mode token should be accepted in local dev."""
        mock_config.WORKOS_CLIENT_ID = "client_test123"
        mock_config.DEV_ENV = "dev"
        token = json.dumps({"sub": "user-abc", "email": "a@b.com"})
        user = verify_workos_token(token)
        assert user is not None
        assert user.user_id == "user-abc"
        assert user.email == "a@b.com"

    @patch("api_server.auth.workos_auth.global_config")
    def test_no_client_id_returns_none(self, mock_config):
        mock_config.WORKOS_CLIENT_ID = None
        assert verify_workos_token("anything") is None

    @patch("api_server.auth.workos_auth.global_config")
    @patch("api_server.auth.workos_auth._get_jwks_client")
    def test_valid_rs256_token(self, mock_jwks, mock_config):
        mock_config.WORKOS_CLIENT_ID = "client_123"
        mock_config.WORKOS_API_KEY = None

        private_key, public_key = _generate_rsa_keypair()

        payload = {
            "sub": "user-rs256",
            "email": "rs@test.com",
            "aud": "client_123",
            "iss": "https://api.workos.com",
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, private_key, algorithm="RS256")

        class FakeSigningKey:
            key = public_key

        mock_jwks.return_value.get_signing_key_from_jwt.return_value = FakeSigningKey()

        user = verify_workos_token(token)
        assert user is not None
        assert user.user_id == "user-rs256"
        assert user.email == "rs@test.com"

    @patch("api_server.auth.workos_auth.global_config")
    @patch("api_server.auth.workos_auth._get_jwks_client")
    def test_expired_token_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_CLIENT_ID = "client_123"
        mock_config.WORKOS_API_KEY = None

        private_key, public_key = _generate_rsa_keypair()

        payload = {
            "sub": "user-expired",
            "aud": "client_123",
            "iss": "https://api.workos.com",
            "exp": int(time.time()) - 3600,
        }
        token = pyjwt.encode(payload, private_key, algorithm="RS256")

        class FakeSigningKey:
            key = public_key

        mock_jwks.return_value.get_signing_key_from_jwt.return_value = FakeSigningKey()

        user = verify_workos_token(token)
        assert user is None

    @patch("api_server.auth.workos_auth.global_config")
    @patch("api_server.auth.workos_auth._get_jwks_client")
    def test_wrong_audience_rejected(self, mock_jwks, mock_config):
        mock_config.WORKOS_CLIENT_ID = "client_123"
        mock_config.WORKOS_API_KEY = None

        private_key, public_key = _generate_rsa_keypair()

        payload = {
            "sub": "user-wrong-aud",
            "aud": "wrong_client",
            "iss": "https://api.workos.com",
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, private_key, algorithm="RS256")

        class FakeSigningKey:
            key = public_key

        mock_jwks.return_value.get_signing_key_from_jwt.return_value = FakeSigningKey()

        user = verify_workos_token(token)
        assert user is None
