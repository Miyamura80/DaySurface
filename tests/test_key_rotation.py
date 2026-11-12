"""Tests for rotating ``GOOGLE_TOKEN_ENC_KEY`` (``db.key_rotation``)."""

import asyncio
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet, InvalidToken

from api_server import runner
from api_server.runner import rotation_lifespan
from common import global_config, token_encryption
from common.token_encryption import (
    FernetEncryption,
    get_default_encryption,
    key_fingerprint,
)
from db.key_rotation import reencrypt_stale_rows
from db.models.google_tokens import GoogleToken
from db.models.thread_curation import ThreadCuration
from db.models.webhooks import WebhookSubscription
from tests.gmail_fakes import patch_db
from tests.test_template import TestTemplate

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def _keys(active: str | None, old: str | None):
    return (
        patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_KEY", active),
        patch.object(token_encryption.global_config, "GOOGLE_TOKEN_ENC_OLD_KEYS", old),
    )


class TestMultiKeyEncryption(TestTemplate):
    def test_old_key_decrypts_new_key_encrypts(self):
        old_ct = FernetEncryption(OLD).encrypt("rt")
        enc = FernetEncryption(NEW, old_keys=[OLD])
        assert enc.decrypt(old_ct) == "rt"
        new_ct = enc.encrypt("rt")
        assert FernetEncryption(NEW).decrypt(new_ct) == "rt"
        with pytest.raises(InvalidToken):
            FernetEncryption(OLD).decrypt(new_ct)

    def test_rotate_moves_ciphertext_to_active_key(self):
        old_ct = FernetEncryption(OLD).encrypt("rt")
        rotated = FernetEncryption(NEW, old_keys=[OLD]).rotate(old_ct)
        assert FernetEncryption(NEW).decrypt(rotated) == "rt"

    def test_rows_are_stamped_with_the_active_key_fingerprint(self):
        active, old = _keys(NEW, OLD)
        with active, old:
            enc = get_default_encryption()
        assert enc is not None
        assert enc.key_id == key_fingerprint(NEW)
        assert key_fingerprint(NEW) != key_fingerprint(OLD)
        assert NEW not in enc.key_id


class TestOldKeysConfig(TestTemplate):
    @staticmethod
    def _validate(**fields):
        cfg = global_config.model_copy(update=fields)
        return cfg._validate_token_enc_key()  # ty: ignore[call-non-callable]

    def test_blank_entries_are_dropped(self):
        cfg = self._validate(
            GOOGLE_TOKEN_ENC_KEY=NEW, GOOGLE_TOKEN_ENC_OLD_KEYS=f" {OLD} ,,"
        )
        assert cfg.GOOGLE_TOKEN_ENC_OLD_KEYS == OLD

    def test_old_keys_require_an_active_key(self):
        with pytest.raises(ValueError, match="GOOGLE_TOKEN_ENC_KEY is not"):
            self._validate(GOOGLE_TOKEN_ENC_KEY=None, GOOGLE_TOKEN_ENC_OLD_KEYS=OLD)

    def test_invalid_old_key_fails_at_boot(self):
        with pytest.raises(ValueError, match="valid Fernet keys"):
            self._validate(GOOGLE_TOKEN_ENC_KEY=NEW, GOOGLE_TOKEN_ENC_OLD_KEYS="nope")


class TestReencryptStaleRows(TestTemplate):
    @staticmethod
    def _seed(factory) -> None:
        old = FernetEncryption(OLD)
        stranger = FernetEncryption(Fernet.generate_key().decode())
        s = factory()
        s.add_all(
            [
                GoogleToken(
                    user_id="alice", refresh_token_enc=old.encrypt("rt-a"), key_id="v1"
                ),
                GoogleToken(user_id="revoked", refresh_token_enc=None, key_id="v1"),
                GoogleToken(
                    user_id="dev", refresh_token_enc=b"plain", key_id="plaintext"
                ),
                GoogleToken(
                    user_id="lost", refresh_token_enc=stranger.encrypt("x"), key_id="v1"
                ),
                WebhookSubscription(
                    id="sub1",
                    user_id="alice",
                    url="https://a.test",
                    secret_enc=old.encrypt("hmac"),
                ),
                ThreadCuration(
                    user_id="alice",
                    thread_id="t1",
                    summary_enc=old.encrypt("summary"),
                    reasoning_enc=None,
                ),
            ]
        )
        s.commit()

    def test_every_table_moves_to_the_active_key(self):
        active, old = _keys(NEW, OLD)
        with patch_db() as factory, active, old:
            self._seed(factory)
            remaining = reencrypt_stale_rows()

            new_only = FernetEncryption(NEW)
            fp = key_fingerprint(NEW)
            s = factory()
            alice = s.get(GoogleToken, "alice")
            assert new_only.decrypt(alice.refresh_token_enc) == "rt-a"
            assert alice.key_id == fp
            assert s.get(GoogleToken, "revoked").key_id == fp
            sub = s.get(WebhookSubscription, "sub1")
            assert new_only.decrypt(sub.secret_enc) == "hmac"
            assert sub.key_id == fp
            cur = s.get(ThreadCuration, ("alice", "t1"))
            assert new_only.decrypt(cur.summary_enc) == "summary"
            assert cur.reasoning_enc is None
            assert cur.key_id == fp
            # Plaintext dev rows are left alone; undecryptable rows are reported.
            dev = s.get(GoogleToken, "dev")
            assert (dev.refresh_token_enc, dev.key_id) == (b"plain", "plaintext")
            assert s.get(GoogleToken, "lost").key_id == "v1"
        assert remaining == {
            "google_tokens": 1,
            "webhook_subscriptions": 0,
            "thread_curation": 0,
        }

    def test_second_pass_is_a_no_op(self):
        active, old = _keys(NEW, OLD)
        with patch_db() as factory, active, old:
            self._seed(factory)
            reencrypt_stale_rows()
            with patch.object(FernetEncryption, "rotate", side_effect=AssertionError):
                # Only the undecryptable row is retried.
                with pytest.raises(AssertionError):
                    reencrypt_stale_rows()
                s = factory()
                s.delete(s.get(GoogleToken, "lost"))
                s.commit()
                assert sum(reencrypt_stale_rows().values()) == 0

    def test_concurrent_reconnect_is_not_overwritten(self):
        """A token rewritten mid-rotation keeps its new value."""
        active, old = _keys(NEW, OLD)
        with patch_db() as factory, active, old:
            s = factory()
            s.add(
                GoogleToken(
                    user_id="alice",
                    refresh_token_enc=FernetEncryption(OLD).encrypt("stale"),
                )
            )
            s.commit()
            fresh = FernetEncryption(NEW).encrypt("fresh")
            real_rotate = FernetEncryption.rotate

            def reconnect_then_rotate(self, ciphertext):
                w = factory()
                row = w.get(GoogleToken, "alice")
                row.refresh_token_enc = fresh
                row.key_id = key_fingerprint(NEW)
                w.commit()
                return real_rotate(self, ciphertext)

            with patch.object(FernetEncryption, "rotate", reconnect_then_rotate):
                remaining = reencrypt_stale_rows()
            row = factory().get(GoogleToken, "alice")
            assert FernetEncryption(NEW).decrypt(row.refresh_token_enc) == "fresh"
        assert remaining["google_tokens"] == 0

    def test_no_key_configured_does_nothing(self):
        active, old = _keys(None, None)
        with active, old:
            assert reencrypt_stale_rows() == {}


class TestRotationLifespan(TestTemplate):
    @staticmethod
    def _enter(old_keys: str | None) -> bool:
        ran = []

        async def go():
            with (
                patch.object(
                    token_encryption.global_config,
                    "GOOGLE_TOKEN_ENC_OLD_KEYS",
                    old_keys,
                ),
                patch.object(runner, "_run_key_rotation", lambda: ran.append(True)),
            ):
                async with rotation_lifespan(None):
                    await asyncio.sleep(0.05)

        asyncio.run(go())
        return bool(ran)

    def test_runs_only_while_old_keys_are_set(self):
        assert self._enter(OLD) is True
        assert self._enter(None) is False
