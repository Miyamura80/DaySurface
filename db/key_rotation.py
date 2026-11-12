"""Re-encrypt stored ciphertext under the active ``GOOGLE_TOKEN_ENC_KEY``.

Rotating the key is two deploys:

1. Set ``GOOGLE_TOKEN_ENC_KEY`` to a new key and ``GOOGLE_TOKEN_ENC_OLD_KEYS``
   to the old one. On startup, ``api_server.runner.rotation_lifespan`` runs
   :func:`reencrypt_stale_rows`, which re-encrypts every row
   still on a retired key and logs how many remain.
2. Once the log reports ``0 rows still on retired keys``, remove
   ``GOOGLE_TOKEN_ENC_OLD_KEYS``. The old key then decrypts nothing.

Each row is swapped with a compare-and-set update (the ``WHERE`` repeats the
ciphertext that was read), so a token rewritten by a reconnect mid-rotation is
never overwritten with its stale value, and concurrent replicas running the
same pass are harmless.
"""

from typing import Any

from cryptography.fernet import InvalidToken
from loguru import logger as log
from sqlalchemy import inspect, select, update

from common.token_encryption import FernetEncryption, get_default_encryption
from db.engine import use_db_session
from db.models.google_tokens import GoogleToken
from db.models.thread_curation import ThreadCuration
from db.models.webhooks import WebhookSubscription

# Every table holding ciphertext from common.token_encryption, with its columns.
_ENCRYPTED: tuple[tuple[Any, tuple[str, ...]], ...] = (
    (GoogleToken, ("refresh_token_enc",)),
    (WebhookSubscription, ("secret_enc",)),
    (ThreadCuration, ("summary_enc", "reasoning_enc")),
)
# Dev-only rows stored by PlaintextEncryption; there is no key to rotate.
_PLAINTEXT_KEY_ID = "plaintext"


def reencrypt_stale_rows() -> dict[str, int]:
    """Re-encrypt every row not yet on the active key.

    Returns, per table, how many rows are still on a retired key afterwards:
    rows that no known key decrypts. A row rewritten concurrently is skipped,
    not counted: every writer encrypts under the active key. Empty when no
    encryption key is configured.
    """
    enc = get_default_encryption()
    if enc is None:
        return {}
    return {
        model.__tablename__: _reencrypt_table(enc, model, columns)
        for model, columns in _ENCRYPTED
    }


def _reencrypt_table(
    enc: FernetEncryption, model: Any, columns: tuple[str, ...]
) -> int:
    pk = list(inspect(model).primary_key)
    cols = [getattr(model, c) for c in columns]
    stale = (model.key_id != enc.key_id) & (model.key_id != _PLAINTEXT_KEY_ID)
    with use_db_session() as session:
        rows = session.execute(select(*pk, *cols).where(stale)).all()
        remaining = 0
        for row in rows:
            values = row._mapping
            try:
                new = {
                    c: None if values[c] is None else enc.rotate(values[c])
                    for c in columns
                }
            except InvalidToken:
                remaining += 1
                continue
            unchanged = [
                col.is_(None) if values[col.key] is None else col == values[col.key]
                for col in cols
            ]
            result = session.execute(
                update(model)
                .where(*(col == values[col.key] for col in pk), stale, *unchanged)
                .values(key_id=enc.key_id, **new)
            )
            session.commit()
            if result.rowcount != 1:  # ty: ignore[unresolved-attribute]
                log.info(
                    "{}: row changed during rotation; skipped", model.__tablename__
                )
    if remaining:
        log.warning(
            "{}: {} rows could not be re-encrypted", model.__tablename__, remaining
        )
    return remaining
