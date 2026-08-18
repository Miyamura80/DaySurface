"""Tests for the ``common.token_encryption`` module."""

import subprocess
import sys
from pathlib import Path
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

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class TestTokenEncKeyValidation(TestTemplate):
    """The config validator must reject a malformed GOOGLE_TOKEN_ENC_KEY at boot.

    Regression for the ``.env.example`` inline-comment leak: an empty value with
    a same-line ``# comment`` was parsed by python-dotenv as the value itself, so
    ``GOOGLE_TOKEN_ENC_KEY`` became a comment string that was truthy but not a
    valid Fernet key - deferring a confusing ``ValueError`` deep inside a later
    encrypt call. Env vars are the highest-priority settings source, so a
    subprocess exercises the real config-load path.
    """

    def _load_config(self, key: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from common import global_config; "
                "print(repr(global_config.GOOGLE_TOKEN_ENC_KEY))",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={"PATH": "/usr/bin:/bin", "GOOGLE_TOKEN_ENC_KEY": key},
            check=False,
        )

    def test_garbage_key_fails_loudly_at_boot(self):
        result = self._load_config(
            "# Fernet key (url-safe base64, 32 bytes) for refresh-token encryption"
        )
        assert result.returncode != 0, result.stdout
        assert "GOOGLE_TOKEN_ENC_KEY must be a valid Fernet key" in result.stderr

    def test_blank_key_normalizes_to_none(self):
        result = self._load_config("   ")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None", result.stdout

    def test_valid_key_loads(self):
        key = Fernet.generate_key().decode()
        result = self._load_config(key)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == repr(key), result.stdout
