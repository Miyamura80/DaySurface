"""Tests for the ``common.token_encryption`` module."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from common import token_encryption
from common.token_encryption import (
    FernetEncryption,
    PlaintextEncryption,
    get_default_encryption,
    require_encryption,
)
from tests.test_template import TestTemplate


class TestFernetEncryption(TestTemplate):
    def test_round_trip(self):
        key = Fernet.generate_key().decode()
        enc = FernetEncryption(key)
        plaintext = "refresh-token-123"
        ciphertext = enc.encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        assert ciphertext != plaintext.encode()
        assert enc.decrypt(ciphertext) == plaintext

    def test_explicit_key_id(self):
        key = Fernet.generate_key().decode()
        enc = FernetEncryption(key, key_id="v2")
        assert enc.key_id == "v2"

    def test_default_key_id(self):
        key = Fernet.generate_key().decode()
        enc = FernetEncryption(key)
        assert enc.key_id == "v1"


class TestPlaintextEncryption(TestTemplate):
    def test_round_trip(self):
        enc = PlaintextEncryption()
        plaintext = "refresh-token-xyz"
        ciphertext = enc.encrypt(plaintext)
        assert enc.decrypt(ciphertext) == plaintext
        assert enc.key_id == "plaintext"


class TestRequireEncryption(TestTemplate):
    def test_get_default_returns_none_when_unset(self):
        with patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", None):
            assert get_default_encryption() is None

    def test_get_default_returns_fernet_when_set(self):
        key = Fernet.generate_key().decode()
        with patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", key):
            enc = get_default_encryption()
        assert isinstance(enc, FernetEncryption)

    def test_require_raises_in_prod_without_key(self):
        with (
            patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", None),
            patch.object(token_encryption.global_config, "DEV_ENV", "prod"),
            pytest.raises(RuntimeError, match="GOOGLE_TOKEN_ENC_KEY"),
        ):
            require_encryption()

    def test_require_falls_back_in_dev(self):
        with (
            patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", None),
            patch.object(token_encryption.global_config, "DEV_ENV", "dev"),
        ):
            enc = require_encryption()
        assert isinstance(enc, PlaintextEncryption)
        assert enc.key_id == "plaintext"

    def test_require_falls_back_in_local(self):
        with (
            patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", None),
            patch.object(token_encryption.global_config, "DEV_ENV", "local"),
        ):
            enc = require_encryption()
        assert isinstance(enc, PlaintextEncryption)

    def test_require_returns_fernet_when_key_present(self):
        key = Fernet.generate_key().decode()
        with patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", key):
            enc = require_encryption()
        assert isinstance(enc, FernetEncryption)
