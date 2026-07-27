"""Enforce a max line count on source files.

The single source of truth for the source-file line-length limit, shared by
`make ci` (file_len_check), the Lint GitHub workflow, and prek. Scans the whole
tree for .py/.ts/.tsx; the limit and per-file exclusions come from
[tool.file_length] in pyproject.toml.
"""

from __future__ import annotations

import os
import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_SUFFIXES = (".py", ".ts", ".tsx")
# Directory names skipped wherever they appear in the tree. TypeScript sources
# live beside deep, per-package `node_modules` / `dist` / `.next` folders, so a
# top-level-only skip is not enough - match these at any depth.
SKIP_DIRS = {
    ".git",
    ".venv",
    ".uv_cache",
    ".uv-cache",
    ".uv_tools",
    ".uv-tools",
    ".cache",
    "node_modules",
    ".next",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
}


def load_config() -> tuple[int, set[str]]:
    pyproject = REPO_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    cfg = data.get("tool", {}).get("file_length", {})
    max_lines = cfg.get("max_lines", 500)
    exclude = set(cfg.get("exclude", []))
    return max_lines, exclude


def main() -> int:
    max_lines, exclude = load_config()
    violations: list[tuple[pathlib.Path, int]] = []

    # Prune skipped directories in place so os.walk never descends into
    # node_modules/.venv/etc. - the TypeScript trees hold tens of thousands of
    # files we would otherwise stat only to discard.
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(SOURCE_SUFFIXES):
                continue
            path = pathlib.Path(dirpath) / name
            rel = path.relative_to(REPO_ROOT)
            if rel.as_posix() in exclude:
                continue
            try:
                line_count = len(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines()
                )
            except OSError as e:
                print(f"  Warning: could not read {rel}: {e}")
                continue
            if line_count > max_lines:
                violations.append((rel, line_count))

    if violations:
        print(
            f"File length check failed: {len(violations)} file(s) exceed {max_lines} lines"
        )
        for rel_path, count in sorted(violations):
            print(f"  {rel_path}: {count} lines")
        print(
            "Refactor large files into smaller modules, "
            "or add to [tool.file_length] exclude in pyproject.toml."
        )
        return 1

    print(f"File length check passed (all files <= {max_lines} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
