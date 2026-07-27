"""Shell completions install command."""

import os
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)

# Click derives the completion env var from the program name, so this constant
# has to stay in step with the console script in pyproject.toml. Deriving both
# the binary lookup and the env var from it keeps them from drifting apart on a
# rename - a mismatch makes the subprocess emit nothing and completions fail.
_CLI_NAME = "daysurface"
_COMPLETE_ENV_VAR = f"_{_CLI_NAME.upper().replace('-', '_')}_COMPLETE"


class Shell(StrEnum):
    bash = "bash"
    zsh = "zsh"
    fish = "fish"


_RC_FILES = {
    Shell.bash: Path.home() / ".bashrc",
    Shell.zsh: Path.home() / ".zshrc",
    Shell.fish: Path.home() / ".config" / "fish" / "config.fish",
}


def _generate_completion_script(shell: Shell) -> str:
    """Generate completion script by invoking Typer's built-in mechanism."""
    # Typer's instruction format is "<action>_<shell>". "source_*" emits the
    # completion script; "complete_*" asks it to actually complete a command
    # line and dies with KeyError: COMP_WORDS outside a real completion.
    source_map = {
        Shell.bash: "source_bash",
        Shell.zsh: "source_zsh",
        Shell.fish: "source_fish",
    }
    executable = shutil.which(_CLI_NAME) or sys.argv[0]
    result = subprocess.run(
        [executable],
        capture_output=True,
        text=True,
        env={**os.environ, _COMPLETE_ENV_VAR: source_map[shell]},
    )
    return result.stdout


@app.command()
def install(
    shell: Annotated[Shell, typer.Argument(help="Shell to install completions for.")],
) -> None:
    """Install shell completions for daysurface."""
    script = _generate_completion_script(shell)
    if not script.strip():
        console.print("[yellow]Could not generate completion script.[/yellow]")
        console.print(
            "Try using Typer's built-in: [bold]daysurface --install-completion[/bold]"
        )
        return

    rc_file = _RC_FILES[shell]

    if rc_file.exists() and "# daysurface completions" in rc_file.read_text():
        console.print(f"[yellow]Completions already installed in {rc_file}[/yellow]")
        return

    rc_file.parent.mkdir(parents=True, exist_ok=True)
    with open(rc_file, "a") as f:
        f.write(f"\n# daysurface completions\n{script}\n")

    console.print("[green]Completions installed![/green] Restart your shell or run:")
    console.print(f"  source {rc_file}")


@app.command()
def show(
    shell: Annotated[Shell, typer.Argument(help="Shell to show completions for.")],
) -> None:
    """Print the completion script to stdout."""
    script = _generate_completion_script(shell)
    if script.strip():
        typer.echo(script)
    else:
        console.print("[yellow]Could not generate completion script.[/yellow]")
        console.print(
            "Try using Typer's built-in: [bold]daysurface --show-completion[/bold]"
        )
