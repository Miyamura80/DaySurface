"""Scaffold command - creates new command from template."""

import re
from pathlib import Path
from string import Template
from typing import Annotated

import typer
from rich.console import Console

console = Console(stderr=True)

_ROOT_DIR = Path(__file__).parent.parent.parent
_COMMANDS_DIR = _ROOT_DIR / "src" / "cli" / "commands"
_TEMPLATE_PATH = _ROOT_DIR / "templates" / "command.py.tpl"


def init_command(
    name: Annotated[str, typer.Argument(help="Snake_case name for the new command.")],
    desc: Annotated[
        str,
        typer.Option("--desc", "-d", help="Short description of the command."),
    ] = "A new CLI command",
) -> None:
    """Scaffold a new command from the built-in template."""
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        console.print(
            f"[red]Invalid name:[/red] '{name}'. Use snake_case (e.g. my_command)."
        )
        raise typer.Exit(code=1)

    target = _COMMANDS_DIR / f"{name}.py"
    if target.exists():
        console.print(f"[red]File already exists:[/red] {target}")
        raise typer.Exit(code=1)

    template_text = _TEMPLATE_PATH.read_text()
    rendered = Template(template_text).safe_substitute(
        description=desc,
        command_name=name.replace("_", "-"),
    )

    target.write_text(rendered)
    command_name = name.replace("_", "-")
    console.print(f"[green]Created[/green] src/cli/commands/{name}.py")
    console.print(f"Run it with: [bold]mymcp {command_name}[/bold]")
