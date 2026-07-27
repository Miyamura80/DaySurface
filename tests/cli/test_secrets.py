"""Tests for the secrets command."""

from contextlib import ExitStack
from unittest.mock import patch

import keyring.errors
from typer.testing import CliRunner

from src.cli.app import _register_builtin_commands, _register_user_commands, app
from src.cli.commands.secrets import _SERVICE_NAME, _mask_value
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()


class FakeKeyring:
    """Dict-backed fake keyring for testing."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        if (service, key) not in self.store:
            raise keyring.errors.PasswordDeleteError(f"No password for {key}")
        del self.store[(service, key)]


def _apply_patches(stack: ExitStack, fake: FakeKeyring) -> None:
    """Apply all keyring patches onto an ExitStack."""
    stack.enter_context(
        patch("src.cli.commands.secrets.keyring.get_password", fake.get_password)
    )
    stack.enter_context(
        patch("src.cli.commands.secrets.keyring.set_password", fake.set_password)
    )
    stack.enter_context(
        patch("src.cli.commands.secrets.keyring.delete_password", fake.delete_password)
    )


class TestSecrets(TestTemplate):
    def test_set_and_get(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "set", "MY_KEY", "my_secret_value"])
            assert result.exit_code == 0
            assert "Stored" in result.output

            result = runner.invoke(app, ["secrets", "get", "MY_KEY", "--reveal"])
            assert result.exit_code == 0
            assert "my_secret_value" in result.output

    def test_get_masked(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "MY_KEY", "my_secret_value"])
            result = runner.invoke(app, ["secrets", "get", "MY_KEY"])
            assert result.exit_code == 0
            assert "my_secret_value" not in result.output
            assert "MY_KEY=" in result.output

    def test_get_not_found(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "get", "NONEXISTENT"])
            assert result.exit_code == 1
            assert "not found" in result.output
            # Actionable error points at the discovery command.
            assert "secrets list" in result.output

    def test_delete(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "DEL_KEY", "value"])
            result = runner.invoke(app, ["secrets", "delete", "DEL_KEY"])
            assert result.exit_code == 0
            assert "Deleted" in result.output

    def test_delete_not_found(self):
        # Delete is idempotent: removing an absent secret is a no-op success.
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "delete", "NONEXISTENT"])
            assert result.exit_code == 0
            assert "No-op" in result.output

    def test_delete_no_op_untracks_key(self):
        # After a no-op delete, the key must not linger in the tracked list.
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "STALE", "v"])
            # Simulate the value already gone from the backend but still tracked.
            fake.store.pop((_SERVICE_NAME, "STALE"), None)
            result = runner.invoke(app, ["secrets", "delete", "STALE"])
            assert result.exit_code == 0
            listing = runner.invoke(app, ["secrets", "list"])
            assert "STALE" not in listing.output

    def test_set_via_stdin(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app, ["secrets", "set", "STDIN_KEY", "--stdin"], input="piped_value\n"
            )
            assert result.exit_code == 0
            result = runner.invoke(app, ["secrets", "get", "STDIN_KEY", "--reveal"])
            assert "piped_value" in result.output

    def test_set_via_dash_sentinel(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app, ["secrets", "set", "DASH_KEY", "-"], input="dashed_value\n"
            )
            assert result.exit_code == 0
            result = runner.invoke(app, ["secrets", "get", "DASH_KEY", "--reveal"])
            assert "dashed_value" in result.output

    def test_set_empty_stdin_fails_fast(self):
        # Empty pipe must not silently store an empty secret.
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app, ["secrets", "set", "EMPTY_KEY", "--stdin"], input=""
            )
            assert result.exit_code == 1
            assert "no secret value" in result.output
            assert all(key != "EMPTY_KEY" for _service, key in fake.store)

    def test_set_missing_value_non_interactive_errors(self):
        # No value, no tty, no stdin flag: fail fast instead of hanging on a prompt.
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "set", "NO_VAL"], input="")
            assert result.exit_code == 1
            assert "no secret value" in result.output

    def test_set_dry_run(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app, ["--dry-run", "secrets", "set", "DRY_KEY", "val"]
            )
            assert result.exit_code == 0
            assert "DRY RUN" in result.output
            # Nothing was actually written.
            assert all(key != "DRY_KEY" for _service, key in fake.store)

    def test_delete_dry_run(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "KEEP_KEY", "val"])
            result = runner.invoke(app, ["--dry-run", "secrets", "delete", "KEEP_KEY"])
            assert result.exit_code == 0
            assert "DRY RUN" in result.output
            # Still present afterwards.
            result = runner.invoke(app, ["secrets", "get", "KEEP_KEY", "--reveal"])
            assert "val" in result.output

    def test_import_via_stdin(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app,
                ["secrets", "import", "--stdin"],
                input="PIPED_KEY=piped_secret\n",
            )
            assert result.exit_code == 0
            assert "Imported 1" in result.output
            result = runner.invoke(app, ["secrets", "get", "PIPED_KEY", "--reveal"])
            assert "piped_secret" in result.output

    def test_import_dry_run_writes_nothing(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app,
                ["--dry-run", "secrets", "import", "--stdin"],
                input="DRY_IMPORT=v\n",
            )
            assert result.exit_code == 0
            assert "DRY RUN" in result.output
            assert "Would import 1" in result.output
            assert all(key != "DRY_IMPORT" for _service, key in fake.store)

    def test_import_stdin_with_interactive_rejected(self):
        # The two flags contend for the same stdin; fail fast, don't abort opaquely.
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(
                app,
                ["secrets", "import", "--stdin", "--interactive"],
                input="K=v\n",
            )
            assert result.exit_code == 2
            assert "cannot be used with --stdin" in result.output

    def test_list_empty(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "list"])
            assert result.exit_code == 0
            assert "No secrets" in result.output

    def test_list_with_keys(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "KEY_A", "val_a"])
            runner.invoke(app, ["secrets", "set", "KEY_B", "val_b"])
            result = runner.invoke(app, ["secrets", "list"])
            assert result.exit_code == 0
            assert "KEY_A" in result.output
            assert "KEY_B" in result.output

    def test_export(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "EXP_KEY", "exp_value_1234"])
            result = runner.invoke(app, ["secrets", "export", "--reveal"])
            assert result.exit_code == 0
            assert "EXP_KEY=exp_value_1234" in result.output

    def test_export_masked(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "EXP_KEY", "exp_value_1234"])
            result = runner.invoke(app, ["secrets", "export"])
            assert result.exit_code == 0
            assert "EXP_KEY=" in result.output
            assert "exp_value_1234" not in result.output

    def test_mask_value_short(self):
        assert _mask_value("abc") == "***"
        assert _mask_value("abcdefgh") == "********"

    def test_mask_value_long(self):
        masked = _mask_value("my_long_secret_value")
        assert masked.startswith("my_")
        assert masked.endswith("lue")
        assert "*" in masked
        assert len(masked) == len("my_long_secret_value")

    def test_import_secrets(self, tmp_path):
        fake = FakeKeyring()
        env_file = tmp_path / ".env"
        env_file.write_text("IMPORT_KEY=import_value\nSKIP_KEY=placeholder...\n")

        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "import", "--file", str(env_file)])
            assert result.exit_code == 0
            assert "Imported 1" in result.output

            result = runner.invoke(app, ["secrets", "get", "IMPORT_KEY", "--reveal"])
            assert result.exit_code == 0
            assert "import_value" in result.output
