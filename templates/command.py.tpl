"""${description}"""

from typing import Annotated

import typer

from src.cli.state import is_dry_run, is_verbose
from src.utils.output import render


def main(
    name: Annotated[str, typer.Argument(help="A positional argument.")],
    flag: Annotated[
        bool,
        typer.Option("--flag", "-f", help="An example flag."),
    ] = False,
) -> None:
    """${description}"""
    if is_dry_run():
        typer.echo(f"[DRY RUN] Would run ${command_name}")
        return

    result = {"name": name, "flag": flag}

    if is_verbose():
        render(result, title="${command_name}")
    else:
        typer.echo(f"Running ${command_name} with name={name}")
