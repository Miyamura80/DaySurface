"""Tests for headless onboarding CLI behavior."""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ONBOARD = PROJECT_ROOT / "init" / "onboard.py"
_COPYTREE_IGNORE_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".ty",
    "node_modules",
    ".next",
}


def _ignore_worktree_copy(_dir: str, names: list[str]) -> set[str]:
    ignored = _COPYTREE_IGNORE_NAMES & set(names)
    ignored.update(name for name in names if name.startswith(".coverage"))
    return ignored


def _run_onboard(
    *args: str,
    cwd: Path | None = None,
    onboard: Path = ONBOARD,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(onboard), *args],
        cwd=cwd or PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_only_profile_dry_run_defaults_to_cli_surface_only():
    result = _run_onboard("--profile", "cli-only", "--dry-run")

    assert result.returncode == 0
    assert "profile: cli-only" in result.stdout
    assert "- cli" in result.stdout
    assert "auth: false" in result.stdout
    assert "database: false" in result.stdout
    assert "docs: false" in result.stdout
    assert "Prune all payment stacks." in result.stdout


def test_stripe_metering_expands_required_infrastructure():
    result = _run_onboard(
        "--profile",
        "custom",
        "--payments",
        "stripe_metering",
        "--dry-run",
    )

    assert result.returncode == 0
    assert "- stripe_billing" in result.stdout
    assert "- stripe_metering" in result.stdout
    assert "- http_api" in result.stdout
    assert "auth: true" in result.stdout
    assert "database: true" in result.stdout


def test_gmail_example_expands_api_mcp_auth_and_db():
    result = _run_onboard(
        "--profile",
        "custom",
        "--examples",
        "gmail_google_oauth",
        "--dry-run",
    )

    assert result.returncode == 0
    assert "- http_api" in result.stdout
    assert "- mcp" in result.stdout
    assert "- mcp_apps" in result.stdout
    assert "auth: true" in result.stdout
    assert "database: true" in result.stdout


def test_load_onboarding_config_from_yaml(tmp_path: Path):
    path = tmp_path / "onboard.yaml"
    path.write_text(
        "\n".join(
            [
                "profile: cli-only",
                "service_surfaces:",
                "  - cli",
                "  - http_api",
                "payments:",
                "  - x402",
                "docs: true",
            ]
        )
    )

    result = _run_onboard("--config", str(path), "--dry-run")

    assert result.returncode == 0
    assert "profile: cli-only" in result.stdout
    assert "- cli" in result.stdout
    assert "- http_api" in result.stdout
    assert "- x402" in result.stdout
    assert "auth: true" in result.stdout
    assert "database: true" in result.stdout
    assert "docs: true" in result.stdout


def test_headless_apply_prunes_cli_only_copy(tmp_path: Path):
    worktree = tmp_path / "repo"
    shutil.copytree(PROJECT_ROOT, worktree, ignore=_ignore_worktree_copy)
    result = _run_onboard(
        "--profile",
        "cli-only",
        cwd=worktree,
        onboard=worktree / "init" / "onboard.py",
    )

    assert result.returncode == 0
    for rel_path in ("api_server", "mcp_server", "db", "docs", "src/payments"):
        assert not (worktree / rel_path).exists()


def test_headless_apply_prunes_multiline_force_include(tmp_path: Path):
    worktree = tmp_path / "repo"
    shutil.copytree(PROJECT_ROOT, worktree, ignore=_ignore_worktree_copy)
    pyproject = worktree / "pyproject.toml"
    text = pyproject.read_text()
    text = text.replace(
        'force-include = {"mcp_server/apps/gmail_composer/dist/mcp-app.html" = "mcp_server/apps/gmail_composer/dist/mcp-app.html", "mcp_server/apps/gmail_inbox/dist/mcp-app.html" = "mcp_server/apps/gmail_inbox/dist/mcp-app.html"}',
        "\n".join(
            [
                "force-include = {",
                '    "mcp_server/apps/gmail_composer/dist/mcp-app.html" = "mcp_server/apps/gmail_composer/dist/mcp-app.html",',
                '    "mcp_server/apps/gmail_inbox/dist/mcp-app.html" = "mcp_server/apps/gmail_inbox/dist/mcp-app.html",',
                "}",
            ]
        ),
    )
    pyproject.write_text(text)

    result = _run_onboard(
        "--profile",
        "cli-only",
        cwd=worktree,
        onboard=worktree / "init" / "onboard.py",
    )

    assert result.returncode == 0
    rewritten = pyproject.read_text()
    assert "force-include = {}" in rewritten
    assert "mcp_server/apps/gmail_composer" not in rewritten


def test_headless_apply_prunes_cli_surface_for_custom_profile(tmp_path: Path):
    worktree = tmp_path / "repo"
    shutil.copytree(PROJECT_ROOT, worktree, ignore=_ignore_worktree_copy)
    result = _run_onboard(
        "--profile",
        "custom",
        "--surfaces",
        "mcp",
        "--no-auth",
        "--no-database",
        "--no-docs",
        cwd=worktree,
        onboard=worktree / "init" / "onboard.py",
    )

    assert result.returncode == 0
    assert not (worktree / "src/cli").exists()
    assert not (worktree / "tests/cli").exists()
    assert (worktree / "mcp_server").exists()
    pyproject = (worktree / "pyproject.toml").read_text()
    makefile = (worktree / "Makefile").read_text()
    assert 'mymcp = "src.cli.app:main_cli"' not in pyproject
    assert "\ncli:" not in makefile
