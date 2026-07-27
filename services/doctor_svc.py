"""Doctor service - pure business logic."""

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from dotenv import dotenv_values

from models.doctor import (
    CheckResultModel,
    DoctorFixInput,
    DoctorInput,
    DoctorResult,
    DoctorStreamInput,
)
from services import service

_ROOT_DIR = Path(__file__).parent.parent


def _check_python_version() -> CheckResultModel:
    ok = sys.version_info >= (3, 12)
    version_str = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return CheckResultModel(
        name="Python version",
        status="pass" if ok else "fail",
        message=f"{version_str}" if ok else f"{version_str} (need >= 3.12)",
        detail=f"sys.version_info = {sys.version_info}",
    )


def _check_uv_installed() -> CheckResultModel:
    path = shutil.which("uv")
    return CheckResultModel(
        name="uv installed",
        status="pass" if path else "fail",
        message=f"found at {path}" if path else "uv not found on PATH",
        detail=f"shutil.which('uv') = {path}",
    )


def _check_deps_synced() -> CheckResultModel:
    try:
        result = subprocess.run(
            ["uv", "sync", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=30,
        )
        output = result.stdout + result.stderr
        synced = "Would make no changes" in output or result.returncode == 0
        if "Would install" in output or "Would update" in output:
            synced = False
        return CheckResultModel(
            name="Deps synced",
            status="pass" if synced else "warn",
            message="all dependencies synced" if synced else "dependencies out of sync",
            detail=output.strip(),
            fixable=True,
        )
    except FileNotFoundError:
        return CheckResultModel(
            name="Deps synced",
            status="fail",
            message="uv not found, cannot check",
            detail="uv must be installed to check dependencies",
        )
    except subprocess.TimeoutExpired:
        return CheckResultModel(
            name="Deps synced",
            status="warn",
            message="timed out checking dependencies",
            detail="uv sync --dry-run took longer than 30s",
            fixable=True,
        )


def _check_config_parseable() -> CheckResultModel:
    try:
        # Import inside the check on purpose: a failing config load (yaml /
        # validation / import error) is exactly what this check must report.
        from common import global_config  # noqa: PLC0415

        _ = global_config.to_dict()
        return CheckResultModel(
            name="Config parseable",
            status="pass",
            message="global_config loaded successfully",
        )
    except Exception as e:  # noqa: BLE001
        # Doctor diagnostic: report any config-load failure (yaml, validation,
        # missing secrets, import errors) as a single "fail" result for the user.
        return CheckResultModel(
            name="Config parseable",
            status="fail",
            message="failed to load global_config",
            detail=str(e),
        )


def _check_env_exists() -> CheckResultModel:
    env_path = _ROOT_DIR / ".env"
    if not env_path.exists():
        return CheckResultModel(
            name=".env exists",
            status="fail",
            message=".env file not found",
            detail=f"expected at {env_path}",
            fixable=True,
        )
    if env_path.stat().st_size == 0:
        return CheckResultModel(
            name=".env exists",
            status="warn",
            message=".env file is empty",
            detail=f"file at {env_path} has 0 bytes",
            fixable=True,
        )
    return CheckResultModel(
        name=".env exists",
        status="pass",
        message=".env file found",
        detail=f"{env_path} ({env_path.stat().st_size} bytes)",
    )


def _check_api_keys() -> CheckResultModel:
    env_path = _ROOT_DIR / ".env"
    example_path = _ROOT_DIR / ".env.example"

    if not example_path.exists():
        return CheckResultModel(
            name="API keys",
            status="warn",
            message="no .env.example to compare against",
            detail="create .env.example to enable this check",
        )

    if not env_path.exists():
        return CheckResultModel(
            name="API keys",
            status="fail",
            message=".env missing - cannot check keys",
        )

    env_vals = dotenv_values(env_path)
    example_vals = dotenv_values(example_path)

    missing = []
    placeholder = []
    for key, example_value in example_vals.items():
        actual = env_vals.get(key)
        if actual is None or actual == "":
            missing.append(key)
        elif example_value and actual == example_value:
            placeholder.append(key)

    if missing:
        return CheckResultModel(
            name="API keys",
            status="fail",
            message=f"{len(missing)} key(s) missing: {', '.join(missing)}",
            detail=f"missing: {missing}, placeholder: {placeholder}",
        )
    if placeholder:
        return CheckResultModel(
            name="API keys",
            status="warn",
            message=f"{len(placeholder)} key(s) still have placeholder values",
            detail=f"placeholder: {placeholder}",
        )
    return CheckResultModel(
        name="API keys",
        status="pass",
        message="all keys set",
        detail=f"checked {len(example_vals)} key(s)",
    )


def _check_prek_hooks() -> CheckResultModel:
    hook_path = _ROOT_DIR / ".git" / "hooks" / "pre-commit"
    if not hook_path.exists():
        return CheckResultModel(
            name="Pre-commit hooks",
            status="fail",
            message="pre-commit hook not installed",
            detail=f"expected at {hook_path}",
            fixable=True,
        )
    content = hook_path.read_text()
    if "prek" in content:
        return CheckResultModel(
            name="Pre-commit hooks",
            status="pass",
            message="prek hook installed",
        )
    return CheckResultModel(
        name="Pre-commit hooks",
        status="warn",
        message="pre-commit hook exists but does not use prek",
        detail="hook content does not contain 'prek'",
    )


def _check_git_repo() -> CheckResultModel:
    git_dir = _ROOT_DIR / ".git"
    return CheckResultModel(
        name="Git repo",
        status="pass" if git_dir.is_dir() else "fail",
        message="git repository found"
        if git_dir.is_dir()
        else ".git/ directory not found",
        detail=f"checked {git_dir}",
    )


_ALL_CHECKS = [
    _check_python_version,
    _check_uv_installed,
    _check_deps_synced,
    _check_config_parseable,
    _check_env_exists,
    _check_api_keys,
    _check_prek_hooks,
    _check_git_repo,
]


def _fix_deps() -> bool:
    try:
        result = subprocess.run(
            ["uv", "sync"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=120,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fix_env() -> bool:
    env_path = _ROOT_DIR / ".env"
    example_path = _ROOT_DIR / ".env.example"
    if example_path.exists() and not env_path.exists():
        shutil.copy2(example_path, env_path)
        return True
    if not example_path.exists() and not env_path.exists():
        env_path.touch()
        return True
    return False


def _fix_prek() -> bool:
    prek_path = shutil.which("prek")
    if not prek_path:
        return False
    try:
        result = subprocess.run(
            ["prek", "install"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_FIXERS: dict[str, Callable[[], bool]] = {
    "Deps synced": _fix_deps,
    ".env exists": _fix_env,
    "Pre-commit hooks": _fix_prek,
}


def _run_checks() -> list[CheckResultModel]:
    return [check_fn() for check_fn in _ALL_CHECKS]


def _apply_fixers(results: list[CheckResultModel]) -> bool:
    """Run every applicable fixer; return True if any of them succeeded."""
    fixed_any = False
    for r in results:
        if r.status != "pass" and r.fixable and r.name in _FIXERS and _FIXERS[r.name]():
            fixed_any = True
    return fixed_any


def _to_result(results: list[CheckResultModel]) -> DoctorResult:
    has_failures = any(r.status == "fail" for r in results)
    return DoctorResult(checks=results, has_failures=has_failures)


@service(
    name="doctor",
    description="Run health checks on the project environment",
    input_model=DoctorInput,
    output_model=DoctorResult,
)
def doctor(input: DoctorInput) -> DoctorResult:
    """Pure read: run every check and report. Fixing lives in ``doctor_fix``."""
    return _to_result(_run_checks())


@service(
    name="doctor_fix",
    description="Run health checks and attempt to auto-fix fixable issues",
    input_model=DoctorFixInput,
    output_model=DoctorResult,
    mutating=True,
)
def doctor_fix(input: DoctorFixInput) -> DoctorResult:
    """Mutating variant: check, apply fixers (uv sync, .env scaffold, prek
    install), then re-check. Split from ``doctor`` so the plain health check
    stays a pure read (no Idempotency-Key needed) while the fixer path gets
    REST idempotency enforcement.
    """
    results = _run_checks()
    if _apply_fixers(results):
        results = _run_checks()
    return _to_result(results)


def iter_doctor(input: DoctorStreamInput) -> Iterator[CheckResultModel]:
    """Yield each ``CheckResultModel`` as it completes - streaming variant of ``doctor``.

    Several checks shell out (``_check_deps_synced`` waits up to 30s on
    ``uv sync --dry-run``), so a unary caller blocks until every check is done.
    This generator lets a transport surface each result the moment it lands.

    In ``--fix`` mode it yields the initial pass first, then applies the fixers
    and yields the re-checked results, so a consumer sees the full progression.
    The pure ``doctor`` service is intentionally left unchanged and stays the
    shared entry point for CLI/MCP/API; only the SSE route uses this generator.
    """
    results: list[CheckResultModel] = []
    for check_fn in _ALL_CHECKS:
        result = check_fn()
        results.append(result)
        yield result

    if not input.fix:
        return

    if _apply_fixers(results):
        for check_fn in _ALL_CHECKS:
            yield check_fn()
