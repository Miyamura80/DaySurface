# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6.0", "tomli_w>=1.0"]
# ///
"""Sync Claude <-> Codex skills, rules, subagents, and AGENTS.md mirrors.

- Symlinks `.claude/skills/<name>` -> `../../.agents/skills/<name>` for every
  directory under `.agents/skills/`.
- Symlinks `.agents/rules/<name>.md` -> `../../.claude/rules/<name>.md` for every
  non-symlink `.md` file under `.claude/rules/`.
- Regenerates `.codex/agents/<name>.toml` from each `.claude/agents/<name>.md`.
- Symlinks `AGENTS.md` -> `CLAUDE.md` in every git-scoped directory that
  contains a `CLAUDE.md` (gitignored paths and submodules excluded).
  `CLAUDE.md` is the source of truth; Codex reads the `AGENTS.md` mirror.
- Auto-prunes dangling symlinks and orphaned TOMLs silently.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import tomli_w
import yaml

REPO = Path(__file__).resolve().parent.parent
SHARED_SKILLS = REPO / ".agents" / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"
CLAUDE_AGENTS = REPO / ".claude" / "agents"
CODEX_AGENTS = REPO / ".codex" / "agents"
SHARED_RULES = REPO / ".agents" / "rules"
CLAUDE_RULES = REPO / ".claude" / "rules"

# Only used by the non-git fallback walk (see _mirror_candidates). Under git,
# scope is decided by `git ls-files`, which already excludes these when ignored.
# Hidden dirs (`.git`, `.claude`, ...) are skipped separately so the walk can't
# loop through the skill/rule symlinks it just created.
AGENTS_MD_SKIP_DIRS = {
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
}

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)
CLAUDE_ONLY_KEYS = {
    "tools",
    "model",
    "color",
    "allowed-tools",
    "disable-model-invocation",
}

SHARED_SKILL_FORBIDDEN_KEYS = {
    "allowed-tools",
    "disable-model-invocation",
    "user-invocable",
    "context",
    "agent",
    "model",
    "effort",
    "hooks",
    "paths",
    "shell",
    "argument-hint",
}
SHARED_SKILL_FORBIDDEN_BODY_PATTERNS = [
    (re.compile(r"\$ARGUMENTS\b"), "$ARGUMENTS substitution"),
    (re.compile(r"\$[1-9]\b"), "positional arg substitution ($1, $2, ...)"),
    (re.compile(r"\$\{CLAUDE_[A-Z_]+\}"), "${CLAUDE_*} interpolation"),
    (re.compile(r"!`[^`]+`"), "!`cmd` shell preprocessing"),
]
SHARED_SKILL_RAW_BODY_PATTERNS = [
    (re.compile(r"^```!\s*$", re.MULTILINE), "```! shell preprocessing block"),
]


def parse_md(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(f"{path}: missing YAML frontmatter")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"{path}: invalid YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise SystemExit(
            f"{path}: YAML frontmatter must be a mapping, got {type(meta).__name__}"
        )
    return meta, m.group(2).lstrip("\r\n")


def render_toml(meta: dict, body: str, source: Path | None = None) -> str:
    if not meta.get("name"):
        where = f"{source}: " if source else ""
        raise SystemExit(f"{where}missing `name` in frontmatter")
    data = {
        "name": str(meta["name"]),
        "description": str(meta.get("description") or ""),
        "developer_instructions": body.rstrip() + "\n",
    }
    out = tomli_w.dumps(data, multiline_strings=True)
    extras = {k: v for k, v in meta.items() if k in CLAUDE_ONLY_KEYS}
    if extras:
        out += "\n# Claude-only frontmatter (preserved for reference, not used by Codex):\n"
        for k, v in extras.items():
            out += f"# {k} = {v!r}\n"
    return out


def _strip_code(text: str) -> str:
    text = re.sub(
        r"^[ ]{0,3}(`{3,}).*?^[ ]{0,3}\1`*", "", text, flags=re.DOTALL | re.MULTILINE
    )
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "`":
            preceded_by_bang = bool(out) and out[-1] == "!"
            run = 0
            while i + run < n and text[i + run] == "`":
                run += 1
            close = text.find("`" * run, i + run)
            if close == -1 or any(
                text[i + run + k] == "\n" for k in range(close - i - run)
            ):
                out.append(text[i : i + run])
                i += run
            elif preceded_by_bang and run == 1:
                # Preserve `!`cmd`` verbatim so the Claude-only shell-preprocessing
                # pattern can still match after code stripping.
                out.append(text[i : close + run])
                i = close + run
            else:
                i = close + run
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def validate_shared_skill(skill_dir: Path) -> list[str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [f"{skill_dir.relative_to(REPO)}: missing SKILL.md"]
    try:
        meta, body = parse_md(skill_md)
    except SystemExit as e:
        return [str(e)]
    errs: list[str] = []
    bad_keys = SHARED_SKILL_FORBIDDEN_KEYS & set(meta.keys())
    if bad_keys:
        errs.append(
            f"{skill_md.relative_to(REPO)}: Claude-only frontmatter keys in shared skill: {sorted(bad_keys)}"
        )
    for pat, label in SHARED_SKILL_RAW_BODY_PATTERNS:
        if pat.search(body):
            errs.append(
                f"{skill_md.relative_to(REPO)}: body uses Claude-only feature: {label}"
            )
    scan_body = _strip_code(body)
    for pat, label in SHARED_SKILL_FORBIDDEN_BODY_PATTERNS:
        if pat.search(scan_body):
            errs.append(
                f"{skill_md.relative_to(REPO)}: body uses Claude-only feature: {label}"
            )
    if not meta.get("name"):
        errs.append(f"{skill_md.relative_to(REPO)}: missing `name` in frontmatter")
    if not meta.get("description"):
        errs.append(
            f"{skill_md.relative_to(REPO)}: missing `description` in frontmatter"
        )
    return errs


def _validate_all_shared_skills(names: set[str]) -> None:
    errors: list[str] = []
    for name in names:
        errors.extend(validate_shared_skill(SHARED_SKILLS / name))
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)


def _materialize_symlink(name: str) -> str | None:
    link = CLAUDE_SKILLS / name
    target = Path("..") / ".." / ".agents" / "skills" / name
    if link.is_symlink():
        if os.path.normpath(os.readlink(link)) == os.path.normpath(str(target)):
            return None
        link.unlink()
    elif link.exists():
        raise SystemExit(
            f"ERROR: name collision - .claude/skills/{name} is a real directory (Claude-only skill) "
            f"but .agents/skills/{name} also exists (shared skill). Resolve by removing one of them."
        )
    link.symlink_to(target)
    return f"symlinked {link.relative_to(REPO)}"


def sync_skill_symlinks() -> list[str]:
    changes: list[str] = []
    shared_existed = SHARED_SKILLS.exists()
    if not shared_existed:
        SHARED_SKILLS.mkdir(parents=True)
    CLAUDE_SKILLS.mkdir(parents=True, exist_ok=True)

    wanted = {p.name for p in SHARED_SKILLS.iterdir() if p.is_dir()}
    _validate_all_shared_skills(wanted)

    for name in wanted:
        change = _materialize_symlink(name)
        if change:
            changes.append(change)

    # If .agents/skills/ was missing entirely (sparse checkout, manual rm) and we
    # just created it empty, refuse to prune - otherwise we'd silently delete every
    # Claude symlink we manage. User-maintained symlinks pointing elsewhere are
    # left alone regardless (see managed-target check below).
    if not shared_existed and not wanted:
        return changes

    shared_resolved = SHARED_SKILLS.resolve()
    for link in CLAUDE_SKILLS.iterdir():
        if not link.is_symlink() or link.name in wanted:
            continue
        raw_target = os.readlink(link)
        expected_rel = os.path.normpath(
            str(Path("..") / ".." / ".agents" / "skills" / link.name)
        )
        managed = os.path.normpath(raw_target) == expected_rel
        if not managed:
            try:
                resolved = (link.parent / raw_target).resolve(strict=False)
                managed = resolved.parent == shared_resolved
            except OSError:
                managed = False
        if managed:
            link.unlink()
            changes.append(f"pruned dangling {link.relative_to(REPO)}")
    return changes


def sync_rule_symlinks() -> list[str]:
    """Create symlinks from .agents/rules/<name>.md -> ../../.claude/rules/<name>.md."""
    changes: list[str] = []
    rules_existed = CLAUDE_RULES.exists()
    SHARED_RULES.mkdir(parents=True, exist_ok=True)
    CLAUDE_RULES.mkdir(parents=True, exist_ok=True)

    wanted: set[str] = set()
    for rule in CLAUDE_RULES.iterdir():
        if rule.is_symlink() or not rule.is_file() or rule.suffix != ".md":
            continue
        wanted.add(rule.name)
        link = SHARED_RULES / rule.name
        target = Path("..") / ".." / ".claude" / "rules" / rule.name
        if link.is_symlink():
            if os.path.normpath(os.readlink(link)) == os.path.normpath(str(target)):
                continue
            link.unlink()
        elif link.exists():
            raise SystemExit(
                f"ERROR: name collision - .agents/rules/{rule.name} is a real file "
                f"but .claude/rules/{rule.name} also exists. The .claude/rules/ version is the "
                "source of truth; remove the .agents/rules/ copy."
            )
        link.symlink_to(target)
        changes.append(f"symlinked {link.relative_to(REPO)}")

    if not rules_existed and not wanted:
        return changes

    for link in SHARED_RULES.iterdir():
        if link.is_symlink() and link.name not in wanted:
            link.unlink()
            changes.append(f"pruned dangling {link.relative_to(REPO)}")
    return changes


def _points_to_claude(link: Path) -> bool:
    return link.is_symlink() and os.path.normpath(os.readlink(link)) == "CLAUDE.md"


def _git_scoped_files() -> list[str] | None:
    """Repo-relative paths git considers in scope: tracked plus untracked-but-
    not-ignored. Gitignored files and submodule contents are excluded (git does
    not recurse into submodules here). Returns None when git is unavailable or
    REPO is not a git work tree, so the caller can fall back to a walk.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(REPO),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.split("\0") if p]


def _mirror_candidates() -> tuple[set[Path], set[Path]]:
    """(dirs holding a CLAUDE.md, existing AGENTS.md paths) to consider.

    Uses `git ls-files` so gitignored paths and submodules are excluded. Falls
    back to a filesystem walk (hidden + vendored dirs pruned) outside git.
    """
    claude_dirs: set[Path] = set()
    agents_md: set[Path] = set()
    files = _git_scoped_files()
    if files is not None:
        for rel in files:
            p = REPO / rel
            if p.name == "CLAUDE.md":
                claude_dirs.add(p.parent)
            elif p.name == "AGENTS.md":
                agents_md.add(p)
        return claude_dirs, agents_md
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in AGENTS_MD_SKIP_DIRS and not d.startswith(".")
        ]
        d = Path(dirpath)
        if "CLAUDE.md" in filenames:
            claude_dirs.add(d)
        if "AGENTS.md" in filenames:
            agents_md.add(d / "AGENTS.md")
    return claude_dirs, agents_md


def sync_agents_md_symlinks() -> list[str]:
    """Mirror every in-scope `CLAUDE.md` to a sibling `AGENTS.md` symlink.

    `CLAUDE.md` is the source of truth. A pre-existing real `AGENTS.md` (e.g. a
    drifted hand-written copy) is replaced by the managed symlink so the two can
    never disagree. A pre-existing `AGENTS.md` *symlink* is left untouched -
    whether it is the managed mirror or a user-managed link pointing elsewhere -
    so custom link targets are never silently clobbered. An `AGENTS.md` symlink
    left dangling by a removed `CLAUDE.md` is pruned. Gitignored paths and
    submodule contents are out of scope.
    """
    changes: list[str] = []
    claude_dirs, agents_md_paths = _mirror_candidates()

    for d in sorted(claude_dirs):
        if not (d / "CLAUDE.md").is_file():
            continue  # staged deletion; nothing to mirror
        link = d / "AGENTS.md"
        if link.is_symlink():
            # Leave every existing symlink alone - the managed mirror, or a
            # user-managed link pointing elsewhere we won't clobber. This is
            # intentionally more conservative than sync_skill_symlinks' creation
            # path (which re-points wrong-target links); only real drifted files
            # are reconciled below. The shared policy is at prune time.
            continue
        if link.exists():
            link.unlink()  # drifted real AGENTS.md -> replace with managed symlink
        link.symlink_to("CLAUDE.md")
        changes.append(f"symlinked {link.relative_to(REPO)} -> CLAUDE.md")

    for link in sorted(agents_md_paths):
        if (link.parent / "CLAUDE.md").is_file():
            continue
        if _points_to_claude(link):
            link.unlink()
            changes.append(f"pruned dangling {link.relative_to(REPO)}")
    return changes


def sync_agents() -> list[str]:
    changes: list[str] = []
    CODEX_AGENTS.mkdir(parents=True, exist_ok=True)
    CLAUDE_AGENTS.mkdir(parents=True, exist_ok=True)

    wanted = set()
    for md in CLAUDE_AGENTS.glob("*.md"):
        meta, body = parse_md(md)
        toml = CODEX_AGENTS / f"{md.stem}.toml"
        new = render_toml(meta, body, source=md.relative_to(REPO))
        if not toml.exists() or toml.read_text() != new:
            toml.write_text(new)
            changes.append(f"wrote {toml.relative_to(REPO)}")
        wanted.add(toml.name)

    for toml in CODEX_AGENTS.glob("*.toml"):
        if toml.name not in wanted:
            toml.unlink()
            changes.append(f"pruned orphan {toml.relative_to(REPO)}")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Still write changes, but exit non-zero when any were made. Used by the pre-commit hook.",
    )
    args = parser.parse_args()

    changes = (
        sync_skill_symlinks()
        + sync_rule_symlinks()
        + sync_agents()
        + sync_agents_md_symlinks()
    )
    for c in changes:
        print(c)
    if args.check and changes:
        print(
            "sync-agent-config introduced changes; stage them and commit again.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
