"""Helpers for reading CLI values from stdin (the ``-`` / ``--stdin`` convention).

Agents think in pipelines, so every value an agent might want to pipe should be
acceptable on stdin as well as via a flag/argument.
"""

import sys

import typer
from rich.console import Console

_console = Console(stderr=True)


def read_stdin() -> str | None:
    """Read a single piped value from stdin.

    - Fails fast (exit 2) on an interactive terminal: stdin was requested but
      nothing is piped, so a blocking read would hang the caller - the opposite
      of agent-friendly.
    - Strips a trailing line terminator, handling both ``\\n`` and ``\\r\\n``.
    - Returns ``None`` for empty input so callers treat "nothing piped" as a
      missing value and hit their fail-fast path.
    """
    if sys.stdin.isatty():
        _console.print(
            "[red]Error:[/red] --stdin (or '-') given but stdin is a terminal; "
            "pipe a value in, e.g. echo <value> | ..."
        )
        raise typer.Exit(code=2)
    data = sys.stdin.read().rstrip("\r\n")
    return data or None


def resolve_value(value: str | None, *, use_stdin: bool = False) -> str | None:
    """Resolve a CLI value, reading from stdin when requested.

    Reads stdin when ``use_stdin`` is True or when ``value`` is the sentinel
    ``-``. Otherwise returns ``value`` unchanged. Empty piped input resolves to
    ``None`` (a missing value), not an empty string.
    """
    if use_stdin or value == "-":
        return read_stdin()
    return value
