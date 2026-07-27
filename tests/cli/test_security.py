"""Tests for security ratings module."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from src.cli.app import _register_builtin_commands, _register_user_commands, app
from src.cli.security import (
    _score_bar,
    _score_label,
    display_security_panel,
    get_snyk_advisor_url,
    get_snyk_security_url,
    get_socket_url,
    show_first_install_notice,
)
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()


class TestSecurityURLs(TestTemplate):
    def test_snyk_advisor_url_default(self):
        url = get_snyk_advisor_url()
        assert url == "https://snyk.io/advisor/python/mcp-template"

    def test_snyk_advisor_url_custom(self):
        url = get_snyk_advisor_url("my-package")
        assert url == "https://snyk.io/advisor/python/my-package"

    def test_snyk_security_url_default(self):
        url = get_snyk_security_url()
        assert url == "https://security.snyk.io/package/pip/mcp-template"

    def test_snyk_security_url_custom(self):
        url = get_snyk_security_url("my-package")
        assert url == "https://security.snyk.io/package/pip/my-package"

    def test_socket_url_default(self):
        url = get_socket_url()
        assert url == "https://socket.dev/pypi/package/mcp-template"

    def test_socket_url_custom(self):
        url = get_socket_url("my-package")
        assert url == "https://socket.dev/pypi/package/my-package"


class TestScoreDisplay(TestTemplate):
    def test_score_bar_high(self):
        bar = _score_bar(0.85)
        assert "85%" in bar
        assert "green" in bar

    def test_score_bar_medium(self):
        bar = _score_bar(0.50)
        assert "50%" in bar
        assert "yellow" in bar

    def test_score_bar_low(self):
        bar = _score_bar(0.20)
        assert "20%" in bar
        assert "red" in bar

    def test_score_label_healthy(self):
        label = _score_label(0.90)
        assert "Healthy" in label

    def test_score_label_good(self):
        label = _score_label(0.75)
        assert "Good" in label

    def test_score_label_fair(self):
        label = _score_label(0.50)
        assert "Fair" in label

    def test_score_label_needs_attention(self):
        label = _score_label(0.20)
        assert "Needs attention" in label


class TestSecurityCommand(TestTemplate):
    def test_security_command_runs(self):
        with patch("src.cli.security._fetch_snyk_score", return_value=None):
            result = runner.invoke(app, ["security"])
        assert result.exit_code == 0

    def test_security_command_output_contains_links(self):
        with patch("src.cli.security._fetch_snyk_score", return_value=None):
            result = runner.invoke(app, ["security"])
        assert "snyk.io" in result.output
        assert "socket.dev" in result.output

    def test_security_command_with_score(self):
        with patch("src.cli.security._fetch_snyk_score", return_value=0.85):
            result = runner.invoke(app, ["security"])
        assert "85%" in result.output

    def test_security_in_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "security" in result.output


class TestSecurityPanel(TestTemplate):
    def test_display_security_panel_no_crash(self):
        """Panel renders without error when network calls are mocked."""
        with patch("src.cli.security._fetch_snyk_score", return_value=None):
            display_security_panel()

    def test_display_security_panel_with_score(self):
        with patch("src.cli.security._fetch_snyk_score", return_value=0.72):
            display_security_panel()


class TestFirstInstallNotice(TestTemplate):
    def test_first_install_notice_shown_once(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{}")

        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._CONFIG_DIR", tmp_path),
        ):
            show_first_install_notice()
            state = json.loads(state_file.read_text())
            assert state["security_notice_shown"] is True

            # Second call should not print again (idempotent)
            show_first_install_notice()

    def test_first_install_notice_skips_if_already_shown(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"security_notice_shown": True}))

        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._CONFIG_DIR", tmp_path),
            patch("src.cli.security.console") as mock_console,
        ):
            show_first_install_notice()
            mock_console.print.assert_not_called()
