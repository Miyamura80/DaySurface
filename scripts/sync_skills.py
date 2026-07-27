# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Sync the repo-root `skills/` source of truth into the landing page.

`skills/<name>/SKILL.md` is the single source of truth for every self-published
agent skill (the layout skills.sh crawls on GitHub). This script mirrors each
skill into the landing page's discovery tree so the same content is served at
`https://gmailmcp.com/.well-known/agent-skills/...`:

- copies `skills/<name>/SKILL.md` -> `landing-page/public/.well-known/agent-skills/<name>/SKILL.md`
- regenerates `landing-page/public/.well-known/agent-skills/index.json` with a
  fresh `sha256:` digest and the `description` pulled from each skill's frontmatter

Run `uv run scripts/sync_skills.py` to regenerate, or `--check` (used by prek and
CI) to fail if the generated files have drifted from the source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SKILLS_SRC = REPO / "skills"
WELL_KNOWN = REPO / "landing-page" / "public" / ".well-known" / "agent-skills"
INDEX_SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md is missing YAML frontmatter")
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm) or {}
    if "name" not in data or "description" not in data:
        raise ValueError("SKILL.md frontmatter needs both `name` and `description`")
    return data


def _build(src: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (file_path -> content) and the index.json entry for one skill."""
    text = src.read_text()
    fm = _parse_frontmatter(text)
    name = fm["name"]
    rel = f"agent-skills/{name}/SKILL.md"
    digest = hashlib.sha256(text.encode()).hexdigest()
    entry = {
        "name": name,
        "type": "skill-md",
        "description": fm["description"],
        "url": f"/.well-known/{rel}",
        "digest": f"sha256:{digest}",
    }
    return {rel: text}, entry


def generate() -> tuple[dict[Path, str], set[str]]:
    """Compute the generated files (path -> content) and the live skill names."""
    sources = sorted(SKILLS_SRC.glob("*/SKILL.md"))
    if not sources:
        raise SystemExit(f"no skills found under {SKILLS_SRC}")
    files: dict[Path, str] = {}
    entries: list[dict[str, str]] = []
    names: set[str] = set()
    for src in sources:
        skill_files, entry = _build(src)
        for rel, content in skill_files.items():
            files[WELL_KNOWN.parent / rel] = content
        entries.append(entry)
        names.add(entry["name"])
    index = {"$schema": INDEX_SCHEMA, "skills": entries}
    files[WELL_KNOWN / "index.json"] = json.dumps(index, indent=2) + "\n"
    return files, names


def stale_mirror_dirs(skill_names: set[str]) -> list[Path]:
    """Mirror skill directories that no longer have a source under `skills/`.

    Without this, deleting a skill leaves its `.well-known/agent-skills/<name>/`
    mirror behind and `--check` would still pass, silently breaking the
    drift-detection guarantee.
    """
    if not WELL_KNOWN.exists():
        return []
    return sorted(
        p for p in WELL_KNOWN.iterdir() if p.is_dir() and p.name not in skill_names
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if generated files differ from the source",
    )
    args = parser.parse_args()

    desired, skill_names = generate()
    drift: list[Path] = []
    for path, content in desired.items():
        current = path.read_text() if path.exists() else None
        if current != content:
            drift.append(path)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)

    stale = stale_mirror_dirs(skill_names)
    for path in stale:
        drift.append(path)
        if not args.check:
            shutil.rmtree(path)

    if args.check:
        if drift:
            listing = "\n".join(f"  - {p.relative_to(REPO)}" for p in drift)
            print(
                "skills are out of sync with skills/ source of truth:\n"
                f"{listing}\n"
                "Run `make sync-skills` and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0

    if drift:
        for path in drift:
            verb = "removed" if path in stale else "updated"
            print(f"{verb} {path.relative_to(REPO)}")
    else:
        print("skills already in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
