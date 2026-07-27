"""Auto-discovery of user commands in the src/cli/commands/ package."""

import importlib
import pkgutil
from pathlib import Path

import typer
from loguru import logger as log


def discover_commands(app: typer.Typer) -> None:
    """Scan src/cli/commands/ and register subcommands on the Typer app.

    - If a module has ``app: typer.Typer`` → added as a sub-app (subcommand group).
    - If a module has ``main()`` callable → registered as a single command.
    - A module-level ``EPILOG`` string → passed as the command's ``--help`` epilog.
    - Filename ``my_tool.py`` → command name ``my-tool``.
    - Modules starting with ``_`` are skipped.
    """
    package_dir = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue

        module = importlib.import_module(f"src.cli.commands.{module_info.name}")  # noqa: TID251 - plugin auto-discovery; see CLAUDE.md "Adding a new feature"
        command_name = module_info.name.replace("_", "-")
        epilog = getattr(module, "EPILOG", None)

        if hasattr(module, "app") and isinstance(module.app, typer.Typer):
            help_text = getattr(module, "__doc__", None) or ""
            app.add_typer(module.app, name=command_name, help=help_text.strip())
        elif hasattr(module, "main") and callable(module.main):
            app.command(name=command_name, epilog=epilog)(module.main)
        else:
            log.warning(
                f"src/cli/commands/{module_info.name}.py has no 'app' (Typer) or 'main()' - skipped"
            )
