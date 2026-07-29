# Adding CLI commands

Drop a Python file in `src/cli/commands/` and it is auto-discovered by
`src/cli/commands/__init__.py` - there is no registry to edit.

Which shape you export decides whether the file becomes one command or a group.

## Single command

Export a `main()` function. The file name becomes the command name, and the
docstring becomes its help text.

```python
# src/cli/commands/hello.py
from typing import Annotated
import typer


def main(name: Annotated[str, typer.Argument(help="Who to greet.")]) -> None:
    """Say hello."""
    typer.echo(f"Hello, {name}!")
```

```bash
uv run daysurface hello World   # Hello, World!
```

## Subcommand group

Export `app = typer.Typer()`. The file name becomes the group name and each
`@app.command()` becomes a subcommand under it.

```python
# src/cli/commands/db.py
import typer

app = typer.Typer()


@app.command()
def migrate() -> None:
    """Run migrations."""
    ...
```

```bash
uv run daysurface db migrate
```

## Where the logic belongs

A command should stay thin. Put the actual work in a `@service`-decorated
function in `services/` and call it from the command, so the same logic ships
on the MCP and HTTP transports without being rewritten. See the "Adding a new
feature" walkthrough in [`CLAUDE.md`](../CLAUDE.md).
