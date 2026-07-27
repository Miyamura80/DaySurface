"""Tests for main CLI entry point and commands."""

from typer.testing import CliRunner

from src.cli.app import _register_builtin_commands, _register_user_commands, app
from tests.test_template import TestTemplate

runner = CliRunner()

# Register commands once for test module
_register_builtin_commands()
_register_user_commands()


class TestCLI(TestTemplate):
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "mymcp" in result.output

    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "CLI Template" in result.output

    def test_greet(self):
        result = runner.invoke(app, ["greet", "Alice"])
        assert result.exit_code == 0
        assert "Hello, Alice!" in result.output

    def test_greet_shout(self):
        result = runner.invoke(app, ["greet", "Alice", "--shout"])
        assert result.exit_code == 0
        assert "HELLO, ALICE!" in result.output

    def test_greet_dry_run(self):
        result = runner.invoke(app, ["--dry-run", "greet", "Bob"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_greet_times(self):
        result = runner.invoke(app, ["greet", "Alice", "--times", "3"])
        assert result.exit_code == 0
        assert result.output.count("Hello, Alice!") == 3

    def test_config_show(self):
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0

    def test_config_get(self):
        result = runner.invoke(app, ["config", "get", "llm_config.cache_enabled"])
        assert result.exit_code == 0

    def test_config_get_nonexistent(self):
        result = runner.invoke(app, ["config", "get", "nonexistent.key"])
        assert result.exit_code == 1
        # Actionable error points at the discovery command.
        assert "config show" in result.output

    def test_config_set_dry_run(self):
        result = runner.invoke(
            app,
            ["--dry-run", "config", "set", "llm_config.cache_enabled", "true"],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_config_set_via_stdin_dry_run(self):
        result = runner.invoke(
            app,
            ["--dry-run", "config", "set", "llm_config.cache_enabled", "--stdin"],
            input="true\n",
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_config_set_missing_value_errors(self):
        result = runner.invoke(app, ["config", "set", "some.key"], input="")
        assert result.exit_code == 1
        assert "no value" in result.output

    def test_config_set_via_dash_sentinel_dry_run(self):
        result = runner.invoke(
            app,
            ["--dry-run", "config", "set", "llm_config.cache_enabled", "-"],
            input="true\n",
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "true" in result.output

    def test_help_shows_examples(self):
        for cmd in (["greet"], ["doctor"], ["config", "set"], ["secrets", "set"]):
            result = runner.invoke(app, [*cmd, "--help"])
            assert result.exit_code == 0
            assert "Examples:" in result.output

    def test_help_examples_use_correct_flag_ordering(self):
        # Global flags (--dry-run/--format) must precede the subcommand, or the
        # example fails with exit 2. Assert on the EPILOG source strings rather
        # than rendered --help output, which word-wraps at the terminal width.
        from src.cli.commands import doctor, greet  # noqa: PLC0415

        assert "mymcp --dry-run greet" in greet.EPILOG
        assert "mymcp greet Ada --dry-run" not in greet.EPILOG
        assert "mymcp --format json doctor" in doctor.EPILOG
        assert "mymcp doctor --format json" not in doctor.EPILOG

    def test_format_json(self):
        result = runner.invoke(app, ["--format", "json", "config", "show"])
        assert result.exit_code == 0

    def test_format_plain(self):
        result = runner.invoke(app, ["--format", "plain", "config", "show"])
        assert result.exit_code == 0

    def test_telemetry_status(self):
        result = runner.invoke(app, ["telemetry", "status"])
        assert result.exit_code == 0

    def test_completions_show(self):
        result = runner.invoke(app, ["completions", "show", "bash"])
        assert result.exit_code == 0
