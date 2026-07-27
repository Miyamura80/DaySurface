"""Helpers for building consistent ``--help`` epilogs with examples.

Examples do most of the work for agents - they pattern-match off a concrete
invocation faster than they read a description, so every command ships a few.
"""


def examples_epilog(*lines: str) -> str:
    """Format example invocations as a Rich-markup epilog block.

    Typer's rich help joins single-newline-separated lines into one paragraph,
    so each example is separated by a blank line to keep it on its own row.
    """
    body = "\n\n".join(f"[cyan]{line}[/cyan]" for line in lines)
    return f"[bold]Examples:[/bold]\n\n{body}"
