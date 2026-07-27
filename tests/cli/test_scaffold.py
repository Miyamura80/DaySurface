"""Tests for the scaffold command."""

import os
from pathlib import Path

from typer.testing import CliRunner

from src.cli.app import _register_builtin_commands, _register_user_commands, app
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()

_COMMANDS_DIR = Path(__file__).parent.parent.parent / "src" / "cli" / "commands"

# Use PID in file names so parallel xdist workers don't collide.
_SUFFIX = os.getpid()


class TestScaffold(TestTemplate):
    def test_init_creates_file(self):
        name = f"test_scaffold_{_SUFFIX}"
        target = _COMMANDS_DIR / f"{name}.py"
        try:
            result = runner.invoke(app, ["init", name, "--desc", "A test command"])
            assert result.exit_code == 0
            assert target.exists()
            content = target.read_text()
            assert "A test command" in content
        finally:
            if target.exists():
                target.unlink()

    def test_init_rejects_bad_name(self):
        result = runner.invoke(app, ["init", "Bad-Name"])
        assert result.exit_code == 1

    def test_init_rejects_duplicate(self):
        name = f"test_dup_{_SUFFIX}"
        target = _COMMANDS_DIR / f"{name}.py"
        try:
            target.write_text("# existing command\n")
            result = runner.invoke(app, ["init", name])
            assert result.exit_code == 1
        finally:
            if target.exists():
                target.unlink()
