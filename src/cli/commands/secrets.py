"""Manage secrets via OS keyring."""

import importlib.metadata
import io
import json
import sys
from typing import Annotated

import keyring
import keyring.errors
import typer
from dotenv import dotenv_values
from rich.console import Console

from src.cli.state import is_dry_run, is_quiet
from src.utils.cli_help import examples_epilog
from src.utils.output import render
from src.utils.stdin import resolve_value

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)


def _get_cli_name() -> str:
    """Derive CLI name from package console_scripts entry point."""
    eps = importlib.metadata.entry_points(group="console_scripts")
    for ep in eps:
        if ep.dist and ep.dist.name == "mcp-template":
            return ep.name
    return "mymcp"


_SERVICE_NAME = _get_cli_name()
_KEYS_META = "__secret_keys__"


def _get_tracked_keys() -> list[str]:
    raw = keyring.get_password(_SERVICE_NAME, _KEYS_META)
    if raw is None:
        return []
    try:
        keys = json.loads(raw)
        return sorted(set(keys))
    except (json.JSONDecodeError, TypeError):
        return []


def _set_tracked_keys(keys: list[str]) -> None:
    keyring.set_password(_SERVICE_NAME, _KEYS_META, json.dumps(sorted(set(keys))))


def _track_key(key: str) -> None:
    keys = _get_tracked_keys()
    if key not in keys:
        keys.append(key)
        _set_tracked_keys(keys)


def _untrack_key(key: str) -> None:
    keys = _get_tracked_keys()
    if key in keys:
        keys.remove(key)
        _set_tracked_keys(keys)


def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


@app.command(
    "set",
    epilog=examples_epilog(
        "mymcp secrets set OPENAI_API_KEY sk-...",
        "echo sk-... | mymcp secrets set OPENAI_API_KEY --stdin",
        "mymcp --dry-run secrets set OPENAI_API_KEY sk-...",
    ),
)
def set_secret(
    key: Annotated[str, typer.Argument(help="Secret key name.")],
    value: Annotated[
        str | None,
        typer.Argument(
            help="Secret value. Use '-' or --stdin to read from stdin; "
            "prompts only on an interactive terminal."
        ),
    ] = None,
    use_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read the secret value from stdin."),
    ] = False,
) -> None:
    """Store a secret in the OS keyring."""
    value = resolve_value(value, use_stdin=use_stdin)
    if value is None:
        if sys.stdin.isatty():
            value = typer.prompt(f"Enter value for {key}", hide_input=True)
        else:
            console.print("[red]Error:[/red] no secret value specified.")
            console.print(
                f"  mymcp secrets set {key} <value>   |   "
                f"echo <value> | mymcp secrets set {key} --stdin"
            )
            raise typer.Exit(code=1)

    if is_dry_run():
        console.print(f"[yellow][DRY RUN][/yellow] Would store secret {key}")
        return

    keyring.set_password(_SERVICE_NAME, key, value)
    _track_key(key)

    if not is_quiet():
        console.print(f"[green]Stored[/green] {key}")


@app.command(
    "get",
    epilog=examples_epilog(
        "mymcp secrets get OPENAI_API_KEY",
        "mymcp secrets get OPENAI_API_KEY --reveal",
    ),
)
def get_secret(
    key: Annotated[str, typer.Argument(help="Secret key name.")],
    reveal: Annotated[
        bool,
        typer.Option("--reveal", "-r", help="Show the full secret value."),
    ] = False,
) -> None:
    """Retrieve a secret from the OS keyring."""
    value = keyring.get_password(_SERVICE_NAME, key)
    if value is None:
        console.print(f"[red]Error:[/red] secret not found: {key}")
        console.print("  List stored secrets: [bold]mymcp secrets list[/bold]")
        raise typer.Exit(code=1)

    display = value if reveal else _mask_value(value)
    typer.echo(f"{key}={display}")


@app.command(
    epilog=examples_epilog(
        "mymcp secrets delete OPENAI_API_KEY",
        "mymcp --dry-run secrets delete OPENAI_API_KEY",
    )
)
def delete(
    key: Annotated[str, typer.Argument(help="Secret key name to delete.")],
) -> None:
    """Remove a secret from the OS keyring (no-op if absent)."""
    if is_dry_run():
        console.print(f"[yellow][DRY RUN][/yellow] Would delete secret {key}")
        return

    try:
        keyring.delete_password(_SERVICE_NAME, key)
    except keyring.errors.PasswordDeleteError:
        # A delete error on a key that is actually gone is the idempotent no-op
        # case. If the secret is still present, the backend genuinely failed -
        # surface that instead of silently claiming a no-op success.
        if keyring.get_password(_SERVICE_NAME, key) is not None:
            raise
        _untrack_key(key)
        if not is_quiet():
            console.print(f"[dim]No-op:[/dim] {key} not present")
        return

    _untrack_key(key)

    if not is_quiet():
        console.print(f"[green]Deleted[/green] {key}")


@app.command("list", epilog=examples_epilog("mymcp --format json secrets list"))
def list_secrets() -> None:
    """List stored secret key names (never values)."""
    keys = _get_tracked_keys()
    if not keys:
        if not is_quiet():
            console.print("No secrets stored.")
        return

    rows = []
    for key in keys:
        value = keyring.get_password(_SERVICE_NAME, key)
        rows.append(
            {
                "Key": key,
                "Status": "set" if value else "empty",
            }
        )

    render(rows, title="Secrets")


def _load_import_values(
    file: str, *, use_stdin: bool, interactive: bool
) -> tuple[dict[str, str | None], str]:
    """Load .env values from stdin or a file, validating the stdin combination."""
    if not use_stdin:
        return dotenv_values(file), file

    # Reading the .env body from stdin consumes it, so per-key confirmation has
    # nowhere to read answers from - reject the contradiction up front.
    if interactive:
        console.print(
            "[red]Error:[/red] --interactive cannot be used with --stdin "
            "(stdin is consumed reading the .env body)."
        )
        raise typer.Exit(code=2)
    if sys.stdin.isatty():
        console.print(
            "[red]Error:[/red] --stdin given but stdin is a terminal; "
            "pipe a .env in, e.g. cat .env | mymcp secrets import --stdin"
        )
        raise typer.Exit(code=2)
    return dotenv_values(stream=io.StringIO(sys.stdin.read())), "stdin"


@app.command(
    "import",
    epilog=examples_epilog(
        "mymcp secrets import --file .env",
        "cat .env | mymcp secrets import --stdin",
        "mymcp --dry-run secrets import --file .env",
    ),
)
def import_secrets(
    file: Annotated[
        str,
        typer.Option("--file", "-f", help="Path to .env file to import."),
    ] = ".env",
    use_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read .env content from stdin instead of a file."),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="Confirm each key before importing."),
    ] = False,
) -> None:
    """Import secrets from a .env file (or stdin) into the OS keyring."""
    values, source = _load_import_values(
        file, use_stdin=use_stdin, interactive=interactive
    )
    if not values:
        console.print(f"[yellow]No values found in {source}[/yellow]")
        return

    dry = is_dry_run()
    imported = 0
    skipped = 0
    for key, value in values.items():
        if value is None or value == "" or value.endswith("..."):
            skipped += 1
            continue

        # Don't prompt during a dry-run preview - it makes no changes to confirm.
        if interactive and not dry:
            confirm = typer.confirm(f"Import {key}?")
            if not confirm:
                skipped += 1
                continue

        if dry:
            imported += 1
            continue

        keyring.set_password(_SERVICE_NAME, key, value)
        _track_key(key)
        imported += 1

    if not is_quiet():
        if dry:
            console.print(
                f"[yellow][DRY RUN][/yellow] Would import {imported} secret(s), "
                f"skipped {skipped}"
            )
        else:
            console.print(
                f"[green]Imported {imported} secret(s)[/green], skipped {skipped}"
            )


@app.command(
    "export",
    epilog=examples_epilog("mymcp secrets export", "mymcp secrets export --reveal"),
)
def export_secrets(
    reveal: Annotated[
        bool,
        typer.Option("--reveal", "-r", help="Show full secret values."),
    ] = False,
) -> None:
    """Export secrets in .env format."""
    keys = _get_tracked_keys()
    if not keys:
        if not is_quiet():
            console.print("No secrets to export.")
        return

    for key in keys:
        value = keyring.get_password(_SERVICE_NAME, key)
        if value is None:
            continue
        display = value if reveal else _mask_value(value)
        typer.echo(f"{key}={display}")
