"""Spinner and progress bar context managers."""

from collections.abc import Generator
from contextlib import contextmanager

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.cli.state import is_quiet

console = Console(stderr=True)


@contextmanager
def spinner(message: str) -> Generator[None, None, None]:
    """Show a spinner while work is in progress. Suppressed in --quiet mode."""
    if is_quiet():
        yield
        return
    with console.status(f"[bold blue]{message}[/bold blue]"):
        yield


@contextmanager
def progress_bar(description: str, total: float) -> Generator[Progress, None, None]:
    """Show a progress bar. Suppressed in --quiet mode."""
    if is_quiet():
        progress = Progress(disable=True)
        with progress:
            progress.add_task(description, total=total)
            yield progress
        return
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        *Progress.get_default_columns(),
        console=console,
    )
    with progress:
        progress.add_task(description, total=total)
        yield progress
