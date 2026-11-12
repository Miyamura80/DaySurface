"""Token encryption abstraction for storing OAuth refresh tokens.

A thin Protocol + Fernet impl. A KMS-backed implementation can be swapped in
later by satisfying ``TokenEncryption``. ``key_id`` lets us roll keys forward
without a destructive migration.

Key rotation: ``GOOGLE_TOKEN_ENC_KEY`` is the active key (it encrypts) and
``GOOGLE_TOKEN_ENC_OLD_KEYS`` lists retired keys that still decrypt. While old
keys are set, ``db.key_rotation`` re-encrypts every stored row under
the active key; once it reports none left, the old keys can be removed.
"""

import hashlib
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet, MultiFernet
from loguru import logger as log

from common import global_config


@runtime_checkable
class TokenEncryption(Protocol):
    """Protocol implemented by every token-encryption backend."""

    @property
    def key_id(self) -> str: ...

    def encrypt(self, plaintext: str) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> str: ...


def key_fingerprint(key: str) -> str:
    """Short, non-reversible id for a Fernet key, stamped on rows as ``key_id``.

    Derived from the key itself, so a row's ``key_id`` always names the key
    that encrypted it and a rotation can never be skipped by forgetting to
    bump a version label.
    """
    return "k" + hashlib.sha256(key.encode()).hexdigest()[:12]


class FernetEncryption:
    """Symmetric Fernet encryption.

    Constructor takes a base64-url-encoded 32-byte Fernet key. ``old_keys`` are
    retired keys: they still decrypt, but everything is encrypted with ``key``.
    """

    def __init__(
        self, key: str, key_id: str = "v1", old_keys: Sequence[str] = ()
    ) -> None:
        keys = (key, *old_keys)
        self._fernet = MultiFernet(
            [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        )
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt ``ciphertext`` (from any known key) under the active key.

        Raises ``cryptography.fernet.InvalidToken`` if no known key decrypts it.
        """
        return self._fernet.rotate(ciphertext)


class PlaintextEncryption:
    """No-op encryption used only as a dev-mode fallback.

    Stores tokens in plaintext bytes. The ``key_id="plaintext"`` marker makes
    these rows trivially identifiable in logs / DB inspections so they cannot
    be mistaken for real ciphertext.
    """

    @property
    def key_id(self) -> str:
        return "plaintext"

    def encrypt(self, plaintext: str) -> bytes:
        return plaintext.encode("utf-8")

    def decrypt(self, ciphertext: bytes) -> str:
        return ciphertext.decode("utf-8")


def old_keys() -> list[str]:
    """Retired keys from ``GOOGLE_TOKEN_ENC_OLD_KEYS`` (comma-separated)."""
    raw = getattr(global_config, "GOOGLE_TOKEN_ENC_OLD_KEYS", None) or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def get_default_encryption() -> FernetEncryption | None:
    """Return a ``FernetEncryption`` from config, or ``None`` if unset.

    Rows are stamped with the active key's fingerprint. Rows written before
    rotation existed carry ``key_id="v1"``; they decrypt the same way and are
    re-encrypted like any other row on a retired key.
    """
    key = getattr(global_config, "GOOGLE_TOKEN_ENC_KEY", None)
    if not key:
        return None
    return FernetEncryption(key, key_id=key_fingerprint(key), old_keys=old_keys())


def require_encryption() -> TokenEncryption:
    """Return an encryption backend or raise (in prod) / fall back to plaintext (dev).

    - prod without key → ``RuntimeError`` (refuses to start)
    - dev/local without key → log warning + ``PlaintextEncryption``
    """
    enc = get_default_encryption()
    if enc is not None:
        return enc

    if global_config.is_dev:
        log.warning(
            "GOOGLE_TOKEN_ENC_KEY is not set; falling back to PlaintextEncryption "
            "for refresh-token storage. NEVER use this in production."
        )
        return PlaintextEncryption()

    raise RuntimeError(
        "GOOGLE_TOKEN_ENC_KEY is required in non-dev environments. "
        "Generate one with: "
        "python -c 'from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())'"
    )
