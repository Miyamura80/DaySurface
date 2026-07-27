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
    env_var = "_MYMCP_COMPLETE"
    source_map = {
        Shell.bash: "complete_bash",
        Shell.zsh: "complete_zsh",
        Shell.fish: "complete_fish",
    }
    mymcp = shutil.which("mymcp") or sys.argv[0]
    result = subprocess.run(
        [mymcp],
        capture_output=True,
        text=True,
        env={**os.environ, env_var: source_map[shell]},
    )
    return result.stdout


@app.command()
def install(
    shell: Annotated[Shell, typer.Argument(help="Shell to install completions for.")],
) -> None:
    """Install shell completions for mymcp."""
    script = _generate_completion_script(shell)
    if not script.strip():
        console.print("[yellow]Could not generate completion script.[/yellow]")
        console.print(
            "Try using Typer's built-in: [bold]mymcp --install-completion[/bold]"
        )
        return

    rc_file = _RC_FILES[shell]

    if rc_file.exists() and "# mymcp completions" in rc_file.read_text():
        console.print(f"[yellow]Completions already installed in {rc_file}[/yellow]")
        return

    rc_file.parent.mkdir(parents=True, exist_ok=True)
    with open(rc_file, "a") as f:
        f.write(f"\n# mymcp completions\n{script}\n")

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
            "Try using Typer's built-in: [bold]mymcp --show-completion[/bold]"
        )
