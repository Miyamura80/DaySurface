"""Manage project configuration."""

from typing import Annotated

import typer
from rich.console import Console

from src.cli.state import is_dry_run
from src.utils.cli_help import examples_epilog
from src.utils.output import render
from src.utils.stdin import resolve_value

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)


@app.command(
    epilog=examples_epilog(
        "mymcp config show",
        "mymcp --format json config show",
    )
)
def show() -> None:
    """Show the full configuration."""
    # Lazy by design: this module is imported on every CLI startup; keep
    # model/service imports out of it so `--help` stays fast.
    from models.config import ConfigShowInput  # noqa: PLC0415
    from services.config_svc import config_show  # noqa: PLC0415

    result = config_show(ConfigShowInput())
    render(result.config, title="Configuration")


@app.command(
    epilog=examples_epilog(
        "mymcp config get llm_config.default_model",
        "mymcp --format json config get llm_config.cache_enabled",
    )
)
def get(
    key: Annotated[
        str,
        typer.Argument(help="Dot-separated config key, e.g. llm_config.cache_enabled"),
    ],
) -> None:
    """Get a single configuration value by dot-separated key."""
    # Lazy by design: keep model/service imports off the CLI startup path.
    from models.config import ConfigGetInput  # noqa: PLC0415
    from services.config_svc import config_get  # noqa: PLC0415

    try:
        result = config_get(ConfigGetInput(key=key))
    except KeyError:
        console.print(f"[red]Error:[/red] config key not found: {key}")
        console.print("  List available keys: [bold]mymcp config show[/bold]")
        raise typer.Exit(code=1) from None

    if isinstance(result.value, dict):
        render(result.value, title=key)
    else:
        typer.echo(result.value)


@app.command(
    "set",
    epilog=examples_epilog(
        "mymcp config set llm_config.cache_enabled true",
        "echo true | mymcp config set llm_config.cache_enabled --stdin",
        "mymcp --dry-run config set llm_config.default_model gpt-4o",
    ),
)
def set_value(
    key: Annotated[str, typer.Argument(help="Dot-separated config key to set.")],
    value: Annotated[
        str | None,
        typer.Argument(help="Value to set. Use '-' or --stdin to read from stdin."),
    ] = None,
    use_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read the value from stdin."),
    ] = False,
) -> None:
    """Set a configuration override (writes to .global_config.yaml)."""
    value = resolve_value(value, use_stdin=use_stdin)
    if value is None:
        console.print("[red]Error:[/red] no value specified.")
        console.print(
            "  mymcp config set <key> <value>   |   "
            "echo <value> | mymcp config set <key> --stdin"
        )
        raise typer.Exit(code=1)

    if is_dry_run():
        console.print(f"[yellow][DRY RUN][/yellow] Would set {key} = {value!r}")
        return

    # Lazy by design: keep model/service imports off the CLI startup path.
    from models.config import ConfigSetInput  # noqa: PLC0415
    from services.config_svc import config_set  # noqa: PLC0415

    result = config_set(ConfigSetInput(key=key, value=value))
    console.print(
        f"[green]Set[/green] {result.key} = {result.coerced_value!r} in .global_config.yaml"
    )
