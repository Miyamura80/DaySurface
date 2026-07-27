"""Main CLI entry point."""

import contextlib
import importlib.metadata
import sys
import time
from enum import StrEnum
from typing import Annotated

import typer

from src.cli.commands import discover_commands
from src.cli.completions import app as completions_app
from src.cli.scaffold import init_command
from src.cli.security import security_command, show_first_install_notice
from src.cli.state import (
    OutputFormat,
    Verbosity,
    dry_run,
    output_format,
    verbosity,
)
from src.cli.telemetry import (
    app as telemetry_app,
)
from src.cli.telemetry import (
    record_event,
    show_first_run_notice,
)
from src.cli.update import update_command
from src.utils.errors import install_error_handler
from src.utils.theme import get_cli_emoji, get_primary_color


class FormatChoice(StrEnum):
    table = "table"
    json = "json"
    plain = "plain"


_FORMAT_MAP = {
    FormatChoice.table: OutputFormat.TABLE,
    FormatChoice.json: OutputFormat.JSON,
    FormatChoice.plain: OutputFormat.PLAIN,
}

app = typer.Typer(
    name="mymcp",
    help="CLI Template - a batteries-included Python CLI.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _load_cli_branding() -> tuple[str, str]:
    """Read emoji and primary color from config. Returns (emoji, primary_color)."""
    return get_cli_emoji(), get_primary_color()


def _version_callback(value: bool) -> None:
    if value:
        version = importlib.metadata.version("mcp-template")
        emoji, _ = _load_cli_branding()
        prefix = f"{emoji} " if emoji else ""
        typer.echo(f"{prefix}mymcp {version}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Increase output verbosity."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress non-essential output."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable debug mode with full tracebacks."),
    ] = False,
    fmt: Annotated[
        FormatChoice,
        typer.Option("--format", "-f", help="Output format."),
    ] = FormatChoice.table,
    dry: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview actions without executing."),
    ] = False,
    version: Annotated[  # noqa: ARG001
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Process global flags before any subcommand."""
    # Set verbosity (always reset so contextvars don't leak between invocations)
    if debug:
        verbosity.set(Verbosity.DEBUG)
    elif quiet:
        verbosity.set(Verbosity.QUIET)
    elif verbose:
        verbosity.set(Verbosity.VERBOSE)
    else:
        verbosity.set(Verbosity.NORMAL)

    # Set output format
    output_format.set(_FORMAT_MAP[fmt])

    # Set dry run
    dry_run.set(dry)

    # Install error handler
    install_error_handler(debug=debug)

    # One-time security notice on first run (after flags are parsed)
    if not quiet:
        show_first_install_notice()

    # One-time telemetry opt-out notice
    if not quiet:
        show_first_run_notice()


_builtins_registered = False
_user_commands_registered = False


def _register_builtin_commands() -> None:
    """Register built-in CLI commands (idempotent)."""
    global _builtins_registered  # noqa: PLW0603
    if _builtins_registered:
        return
    _builtins_registered = True

    app.add_typer(completions_app, name="completions", help="Manage shell completions.")
    app.add_typer(telemetry_app, name="telemetry", help="Manage anonymous telemetry.")
    app.command(name="update")(update_command)
    app.command(name="init")(init_command)
    app.command(name="security")(security_command)


def _register_user_commands() -> None:
    """Discover and register user commands from src/cli/commands/ (idempotent)."""
    global _user_commands_registered  # noqa: PLW0603
    if _user_commands_registered:
        return
    _user_commands_registered = True

    discover_commands(app)


_FLAGS_WITH_VALUE = {"--format", "-f"}


def _detect_command(argv: list[str]) -> str:
    """Extract the subcommand name from CLI args (skip global flags)."""
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if "=" in arg and arg.split("=", 1)[0] in _FLAGS_WITH_VALUE:
            continue
        if arg in _FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return "<root>"


def main_cli() -> None:
    """Entry point called by the console script."""
    _register_builtin_commands()
    _register_user_commands()

    version = importlib.metadata.version("mcp-template")
    emoji, primary = _load_cli_branding()
    prefix = f"{emoji} " if emoji else ""
    app.info.help = (
        f"{prefix}[{primary}]CLI Template[/{primary}] "
        f"[dim]v{version}[/dim] - a batteries-included Python CLI."
    )

    command = _detect_command(sys.argv)
    start = time.monotonic()
    success = True
    try:
        app()
    except SystemExit as exc:
        success = exc.code in (None, 0)
        raise
    except Exception:  # noqa: BLE001
        # Top-level CLI boundary: record telemetry for any failure mode then re-raise.
        success = False
        raise
    finally:
        duration = time.monotonic() - start
        # Best-effort: never let telemetry mask the original error.
        with contextlib.suppress(Exception):
            record_event(command=command, duration=duration, success=success)
