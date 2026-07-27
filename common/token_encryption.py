"""Token encryption abstraction for storing OAuth refresh tokens.

A thin Protocol + Fernet impl. A KMS-backed implementation can be swapped in
later by satisfying ``TokenEncryption``. ``key_id`` lets us roll keys forward
without a destructive migration.
"""

from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet
from loguru import logger as log

from common import global_config


@runtime_checkable
class TokenEncryption(Protocol):
    """Protocol implemented by every token-encryption backend."""

    @property
    def key_id(self) -> str: ...

    def encrypt(self, plaintext: str) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> str: ...


class FernetEncryption:
    """Symmetric Fernet encryption.

    Constructor takes a base64-url-encoded 32-byte Fernet key.
    """

    def __init__(self, key: str, key_id: str = "v1") -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode("utf-8")


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


def get_default_encryption() -> TokenEncryption | None:
    """Return a ``FernetEncryption`` from config, or ``None`` if unset.

    v1 scope: a single active key with ``key_id="v1"``. The ``google_tokens``
    table stamps ``key_id`` on every row so a multi-key backend can decrypt
    legacy rows during rotation; that backend is out of scope here and is the
    KMS upgrade path described in ``mcp_server/MCP_UI_ARCHITECTURE.md``. To
    rotate this v1 key today, force a re-consent by calling
    ``gmail_disconnect`` (sets ``revoked_at``) and then ``gmail_connect`` per
    user. Implementing in-place rotation is intentionally deferred.
    """
    key = getattr(global_config, "GOOGLE_TOKEN_ENC_KEY", None)
    if not key:
        return None
    return FernetEncryption(key)


def require_encryption() -> TokenEncryption:
    """Return an encryption backend or raise (in prod) / fall back to plaintext (dev).

    - prod without key → ``RuntimeError`` (refuses to start)
    - dev/local without key → log warning + ``PlaintextEncryption``
    """
    enc = get_default_encryption()
    if enc is not None:
        return enc

    dev_env = (getattr(global_config, "DEV_ENV", "") or "").lower()
    if dev_env in {"local", "dev"}:
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
