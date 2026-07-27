"""Greet someone - example single command."""

from typing import Annotated

import typer

from src.cli.state import is_dry_run, is_verbose
from src.utils.cli_help import examples_epilog
from src.utils.interactive import interactive_fallback
from src.utils.output import render

EPILOG = examples_epilog(
    "mymcp greet Ada",
    "mymcp greet Ada --shout --times 3",
    "mymcp --dry-run greet Ada",
)


@interactive_fallback
def main(
    name: Annotated[
        str | None, typer.Argument(help="Name of the person to greet.")
    ] = None,
    shout: Annotated[
        bool,
        typer.Option("--shout", "-s", help="SHOUT the greeting."),
    ] = False,
    times: Annotated[
        int,
        typer.Option("--times", "-t", help="Number of times to greet."),
    ] = 1,
) -> None:
    """Greet someone by name."""
    if is_dry_run():
        typer.echo(f"[DRY RUN] Would greet {name}")
        return

    # Lazy by design: this module is imported on every CLI startup; keep
    # model/service imports out of it so `--help` stays fast.
    from models.greet import GreetInput  # noqa: PLC0415
    from services.greet import greet  # noqa: PLC0415

    result = greet(GreetInput(name=name or "", shout=shout, times=times))

    if is_verbose():
        render(
            {"name": name, "shout": shout, "times": times, "greeting": result.message},
            title="Greet Details",
        )
    else:
        for _ in range(result.times):
            typer.echo(result.message)
