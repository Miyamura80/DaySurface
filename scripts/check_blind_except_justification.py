"""Enforce that every `# noqa: BLE001` is paired with a justification comment.

Suppressing the blind-except lint must be a deliberate decision. This script
fails if any `# noqa: BLE001` is not accompanied by a non-empty comment within
the next 3 lines (or trailing on the same line after the noqa marker) that
explains why catching everything is the right call.

Uses ``tokenize`` so `#` inside string literals is never treated as a comment.
"""

from __future__ import annotations

import io
import pathlib
import re
import sys
import token
import tokenize

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT_SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv-test",
    ".uv_cache",
    ".uv-cache",
    ".uv_tools",
    ".uv-tools",
    ".cache",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}
RECURSIVE_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".venv-test"}

NOQA_PATTERN = re.compile(r"#\s*noqa:\s*BLE001\b")
LOOKAHEAD_LINES = 3
MIN_JUSTIFICATION_CHARS = 20


def _comment_body(comment_text: str) -> str:
    """Return the body of a comment token (strip the leading `#`)."""
    return comment_text.lstrip("#").strip()


def _is_justification(body: str) -> bool:
    """A justification is a non-noqa comment with enough substance to explain why."""
    if not body:
        return False
    cleaned = re.sub(r"noqa:[^#\s]*", "", body, flags=re.IGNORECASE).strip()
    cleaned = cleaned.lstrip(":#- \t")
    return len(cleaned) >= MIN_JUSTIFICATION_CHARS


def _trailing_text_after_noqa(comment_text: str) -> str:
    """Return any text written after `# noqa: BLE001` in the same comment."""
    match = NOQA_PATTERN.search(comment_text)
    if not match:
        return ""
    return comment_text[match.end() :].lstrip(":#- \t").strip()


def _collect_comment_tokens(path: pathlib.Path) -> list[tokenize.TokenInfo] | None:
    """Tokenize a Python file and return all COMMENT tokens, or None on failure."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        print(f"  Warning: could not read {path}: {e}", file=sys.stderr)
        return None
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        print(f"  Warning: could not tokenize {path}: {e}", file=sys.stderr)
        return None
    return [tok for tok in tokens if tok.type == token.COMMENT]


def _has_justification(comments: list[tokenize.TokenInfo], idx: int) -> bool:
    """Check the noqa comment itself + comments within LOOKAHEAD_LINES rows."""
    noqa_token = comments[idx]
    inline = _trailing_text_after_noqa(noqa_token.string)
    if len(inline) >= MIN_JUSTIFICATION_CHARS:
        return True

    noqa_row = noqa_token.start[0]
    for follower in comments[idx + 1 :]:
        if follower.start[0] > noqa_row + LOOKAHEAD_LINES:
            break
        if _is_justification(_comment_body(follower.string)):
            return True
    return False


def _iter_python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        parts = rel.parts
        if parts[0] in ROOT_SKIP_DIRS:
            continue
        if any(part in RECURSIVE_SKIP_DIRS for part in parts[:-1]):
            continue
        files.append(path)
    return files


def main() -> int:
    violations: list[tuple[pathlib.Path, int, str]] = []

    for path in _iter_python_files():
        comments = _collect_comment_tokens(path)
        if comments is None:
            continue
        for idx, tok in enumerate(comments):
            if not NOQA_PATTERN.search(tok.string):
                continue
            if not _has_justification(comments, idx):
                rel = path.relative_to(REPO_ROOT)
                violations.append((rel, tok.start[0], tok.string.strip()))

    if violations:
        print(
            f"Blind-except justification check failed: "
            f"{len(violations)} `# noqa: BLE001` site(s) lack a justification comment."
        )
        print(
            f"Each `# noqa: BLE001` must be paired with a comment "
            f"(>={MIN_JUSTIFICATION_CHARS} chars) within {LOOKAHEAD_LINES} lines "
            f"explaining why a broad catch is correct."
        )
        for rel_path, line_no, content in violations:
            print(f"  {rel_path}:{line_no}: {content}")
        return 1

    print("Blind-except justification check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
