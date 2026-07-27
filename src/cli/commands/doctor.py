"""Diagnose project environment health."""

from typing import Annotated

import typer
from rich.console import Console

from src.cli.state import is_quiet, is_verbose
from src.utils.cli_help import examples_epilog
from src.utils.output import render

console = Console(stderr=True)

EPILOG = examples_epilog(
    "mymcp doctor",
    "mymcp doctor --fix",
    "mymcp --format json doctor",
)


def main(
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Attempt to auto-fix fixable issues."),
    ] = False,
) -> None:
    """Run health checks on your project environment."""
    # Lazy by design: this module is imported on every CLI startup; keep
    # model/service imports out of it so `--help` stays fast.
    from models.doctor import DoctorFixInput, DoctorInput  # noqa: PLC0415
    from services.doctor_svc import doctor, doctor_fix  # noqa: PLC0415

    result = doctor_fix(DoctorFixInput()) if fix else doctor(DoctorInput())

    if is_quiet():
        status = "FAIL" if result.has_failures else "OK"
        typer.echo(f"doctor: {status}")
        if result.has_failures:
            for r in result.checks:
                if r.status == "fail":
                    typer.echo(f"  {r.name}: {r.message}")
        if result.has_failures:
            raise typer.Exit(code=1)
        return

    rows = []
    for r in result.checks:
        row = {
            "Check": r.name,
            "Status": r.status,
            "Message": r.message,
        }
        if is_verbose():
            row["Detail"] = r.detail
            row["Fixable"] = "yes" if r.fixable else ""
        rows.append(row)

    render(rows, title="Doctor")

    if result.has_failures:
        raise typer.Exit(code=1)
