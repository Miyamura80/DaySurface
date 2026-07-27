"""Output formatter respecting --format flag."""

import json
from typing import Any

from rich.table import Table

from src.cli.state import OutputFormat, output_format
from src.utils.theme import make_console

console = make_console()


def render(data: Any, title: str = "") -> None:
    """Render data according to the current output format."""
    fmt = output_format.get()
    if fmt == OutputFormat.JSON:
        _render_json(data)
    elif fmt == OutputFormat.PLAIN:
        _render_plain(data, title)
    else:
        _render_table(data, title)


def _render_json(data: Any) -> None:
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    console.print_json(json.dumps(data, default=str))


def _render_plain(data: Any, title: str = "") -> None:
    if title:
        console.print(title)
    if isinstance(data, dict):
        for key, value in data.items():
            console.print(f"{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    console.print(f"{key}: {value}")
                console.print("---")
            else:
                console.print(str(item))
    else:
        console.print(str(data))


def _render_table(data: Any, title: str = "") -> None:
    if isinstance(data, dict):
        table = Table(title=title or None)
        table.add_column("Key", style="primary")
        table.add_column("Value", style="white")
        for key, value in data.items():
            table.add_row(str(key), str(value))
        console.print(table)
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        table = Table(title=title or None)
        columns = [str(k) for k in data[0]]
        for col in columns:
            table.add_column(col, style="primary")
        for row in data:
            table.add_row(*(str(row.get(c, "")) for c in columns))
        console.print(table)
    else:
        _render_plain(data, title)
