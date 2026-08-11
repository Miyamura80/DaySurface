"""Tests for WorkOS JWT verification."""

import json
import time
from unittest.mock import patch

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa

from api_server.auth.workos_auth import verify_workos_token
from common import global_config
from tests.test_template import TestTemplate


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


class TestWorkOSAuth(TestTemplate):
    def test_test_mode_bypass(self):
        """JSON test-mode token is accepted only with the explicit opt-in + dev.

        Patches the real config rather than substituting a MagicMock: the gate
        reads ``global_config.is_dev``, and a mock auto-creates that attribute
        as truthy, so the environment would be decorative and the companion
        tests below would pass no matter what the predicate did.
        """
        token = json.dumps({"sub": "user-abc", "email": "a@b.com"})
        with (
            patch.object(global_config, "WORKOS_CLIENT_ID", "client_test123"),
            patch.object(global_config, "DEV_ENV", "dev"),
            patch.object(global_config, "ALLOW_TEST_TOKENS", True),
        ):
            user = verify_workos_token(token)
        assert user is not None
        assert user.user_id == "user-abc"
        assert user.email == "a@b.com"

    def test_test_mode_bypass_is_refused_outside_dev(self):
        """The unsigned-JSON bypass must never authenticate in production.

        This is the direction that matters and it was untested: the bypass
        accepts an arbitrary ``sub``/``email`` with no signature, so leaving it
        reachable in production is full authentication bypass.
        """
        token = json.dumps({"sub": "attacker", "email": "a@evil.com"})
        for dev_env in ("prod", "production", "staging", ""):
            with (
                patch.object(global_config, "WORKOS_CLIENT_ID", "client_test123"),
                patch.object(global_config, "DEV_ENV", dev_env),
                patch.object(global_config, "ALLOW_TEST_TOKENS", True),
            ):
                assert verify_workos_token(token) is None, dev_env

    def test_test_mode_bypass_off_by_default_even_in_dev(self):
        """Without ALLOW_TEST_TOKENS the unsigned token is rejected, even in dev.

        Guards the fail-open fix: DEV_ENV defaults to "dev", so the bypass must
        not fire on the environment gate alone - it needs the explicit opt-in.
        """
        token = json.dumps({"sub": "attacker", "email": "e@v.il"})
        with (
            patch.object(global_config, "WORKOS_CLIENT_ID", "client_test123"),
            patch.object(global_config, "DEV_ENV", "dev"),
            patch.object(global_config, "ALLOW_TEST_TOKENS", False),
        ):
            assert verify_workos_token(token) is None

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
