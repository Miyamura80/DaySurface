# CLAUDE.md - CLI command conventions

This folder is the **CLI transport**. Commands here are thin wrappers that parse
flags, then call a pure `@service` function in `services/`. Keep business logic
out of this layer.

The CLI is designed to be driven non-interactively by scripts **and AI agents**.
When adding or editing a command, follow the patterns below so the surface stays
agent-friendly and consistent. (Background: these mirror the "Building CLIs for
agents" guidance.)

## Where things live

- `src/cli/commands/<feature>.py` - one command (`main()`) or a command group
  (`app: typer.Typer`). Auto-discovered by `commands/__init__.py`:
  - `main()` callable → single command (`my_tool.py` → `mymcp my-tool`)
  - `app` Typer → subcommand group
  - module-level `EPILOG` string → passed as the command's `--help` epilog
- `src/utils/stdin.py` - `resolve_value()` for the `-` / `--stdin` convention
- `src/utils/cli_help.py` - `examples_epilog()` for `--help` examples
- `src/cli/state.py` - `is_dry_run()`, `is_quiet()`, `is_verbose()` (global flags)
- `src/utils/output.py` - `render()` (honors `--format table|json|plain`)

## The seven rules for every command

### 1. Non-interactive by default; never hang an agent
Prompt **only** when stdin is a TTY. Otherwise fail fast with an actionable
error. Don't call `typer.prompt(...)` unconditionally.

```python
value = resolve_value(value, use_stdin=use_stdin)
if value is None:
    if sys.stdin.isatty():
        value = typer.prompt("Enter value", hide_input=True)
    else:
        console.print("[red]Error:[/red] no value specified.")
        console.print("  mymcp <cmd> <value>  |  echo <value> | mymcp <cmd> --stdin")
        raise typer.Exit(code=1)
```

### 2. Accept stdin for any value an agent might pipe
Add `--stdin` (and honor the `-` sentinel) via `resolve_value()`. Make the
positional value `str | None = None` so it can come from stdin instead.

```python
def set_value(
    key: Annotated[str, typer.Argument(help="...")],
    value: Annotated[str | None, typer.Argument(help="Use '-' or --stdin for stdin.")] = None,
    use_stdin: Annotated[bool, typer.Option("--stdin", help="Read value from stdin.")] = False,
) -> None:
    value = resolve_value(value, use_stdin=use_stdin)
```

### 3. Examples in every `--help`
Attach an epilog. For groups, pass it per `@app.command(epilog=...)`. For a
single-command module, define a module-level `EPILOG`.

```python
from src.utils.cli_help import examples_epilog

# group subcommand
@app.command("set", epilog=examples_epilog(
    "mymcp config set key true",
    "echo true | mymcp config set key --stdin",
))

# single-command module (greet.py / doctor.py)
EPILOG = examples_epilog("mymcp greet Ada", "mymcp greet Ada --shout")
```

### 4. `--dry-run` on anything destructive
Check `is_dry_run()` **before** mutating and return early. Applies to every
command that writes/deletes (config writes, keyring writes, file writes, network
mutations).

```python
if is_dry_run():
    console.print(f"[yellow][DRY RUN][/yellow] Would <action> {target}")
    return
```

### 5. Idempotent where retries are plausible
Agents retry. A delete of an absent thing should be a no-op success (exit 0),
not an error. A repeated create/set should converge, not duplicate.

### 6. Actionable errors
On failure, name the fix or the discovery command. Exit non-zero.

```python
console.print(f"[red]Error:[/red] secret not found: {key}")
console.print("  List stored secrets: [bold]mymcp secrets list[/bold]")
raise typer.Exit(code=1)
```

### 7. Return data on success; respect `--format`
Use `render(...)` for structured output so `--format json` works for machine
consumers. Plain status lines go to `console` (stderr). Don't rely on emojis to
convey meaning.

## Predictable structure

Use **resource + verb** naming (`secrets set`, `secrets get`, `config show`) so
an agent that learns one group can guess the others. Keep flag names and short
aliases consistent across commands (`--stdin`, `--dry-run`, `-f/--format`,
`-r/--reveal`).

## Don'ts

- Don't put service/model imports at module top - keep them lazy inside the
  function so `--help` stays fast (see existing commands for the pattern).
- Don't print secrets or large payloads to stdout by default; mask unless
  `--reveal` is passed.
- Don't add interactive confirmation as the default path; default to the
  non-interactive action and gate prompts behind a flag or a TTY check.

## After changing a command

Run `make ci` and the CLI tests (`uv run pytest tests/cli/`). New behavior needs
a test in `tests/cli/` - cover the stdin path, the dry-run path, and the
fail-fast error for non-interactive use.
