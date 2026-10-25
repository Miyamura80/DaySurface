#!/usr/bin/env python3
"""Fail CI if a Dockerfile or GitHub Actions file uses a mutable reference.

Tags (``node:24-slim``, ``:latest``, ``actions/checkout@v6``) can be moved to
point at different content after we review them, which is how tag-rewrite
supply-chain attacks land. Every external reference must therefore be pinned
to content it cannot change:

- Dockerfile ``FROM`` / ``COPY --from=`` / ``RUN --mount=...,from=`` images:
  ``image:tag@sha256:<digest>``
  (a ``$VAR`` is resolved from its ``ARG`` default, which must itself be pinned)
- Workflow / ``action.yml`` ``uses:``: ``owner/repo@<40-hex commit sha>``
- Workflow ``image:`` / scalar ``container:``: ``image:tag@sha256:<digest>``
  (a ``${{ }}`` expression cannot be verified, so it fails closed)
- Action inputs such as ``bun-version: latest``: name an exact version

Keep the human-readable tag next to the pin (``node:24.11.1-slim@sha256:...``,
``actions/checkout@<sha> # v6.0.3``) so updates stay reviewable.

A reference that genuinely cannot be pinned (an image built locally by the
same repo) is allowed when the line before it is ``# pin-exempt: <reason>``.
The exemption covers images only: an action ``uses:`` always needs a sha.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import PurePosixPath

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
INSTRUCTION = re.compile(r"^\s*(?P<cmd>[A-Za-z]+)\s+(?P<args>.*)$")
ARG_DEFAULT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\S+)$")
VARIABLE = re.compile(
    r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)
COPY_FROM = re.compile(r"--from=(?P<ref>\S+)", re.IGNORECASE)
MOUNT_FROM = re.compile(r"--mount=\S*?\bfrom=(?P<ref>[^\s,]+)", re.IGNORECASE)
USES = re.compile(r"""^\s*(?:-\s*)?["']?uses["']?:\s*(?P<ref>[^\s#]+)""")
IMAGE = re.compile(
    r"""^\s*(?:-\s*)?["']?(?P<key>image|container)["']?:\s*(?P<ref>[^\s#]+)"""
)
LATEST_INPUT = re.compile(
    r"""^\s*(?:-\s*)?["']?(?P<key>[\w-]*version)["']?:\s*["']?latest["']?\s*(?:#.*)?$""",
    re.IGNORECASE,
)
EXEMPT = re.compile(r"^\s*#\s*pin-exempt:\s*\S")


def list_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [name for name in out.split("\x00") if name]


def is_dockerfile(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name.endswith(".dockerignore"):
        return False
    return name.startswith("dockerfile") or name.endswith(".dockerfile")


def is_actions_file(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix not in {".yml", ".yaml"}:
        return False
    if p.parts[:2] == (".github", "workflows"):
        return True
    return p.stem == "action"  # composite / docker action metadata, wherever it lives


def mutable_image(ref: str) -> str | None:
    """Return why an image reference is mutable, or None if it is pinned."""
    ref = ref.strip("\"'")
    if DIGEST.search(ref):
        return None
    tag = ref.rsplit("/", 1)[-1].partition(":")[2]
    if not tag:
        return "untagged (implicitly :latest) and not pinned by digest"
    if tag == "latest":
        return ":latest is mutable"
    return "tag is mutable; append @sha256:<digest>"


def instructions(lines: list[str]) -> list[tuple[int, bool, str]]:
    """Join continuation lines into (first lineno, pin-exempt, instruction text)."""
    out: list[tuple[int, bool, str]] = []
    exempt = False
    buf: list[str] = []
    start = 0
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not buf:
            if not stripped:
                continue
            if stripped.startswith("#"):
                exempt = bool(EXEMPT.match(stripped))
                continue
            start = lineno
        elif stripped.startswith("#"):
            continue  # comment lines inside a continuation are dropped by Docker
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        out.append((start, exempt, " ".join(buf)))
        buf, exempt = [], False
    if buf:
        out.append((start, exempt, " ".join(buf)))
    return out


def resolve(ref: str, args: dict[str, str]) -> str | None:
    """Substitute ``$VAR`` / ``${VAR}`` from ARG defaults; None if any is unset."""
    unresolved = False

    def sub(m: re.Match[str]) -> str:
        nonlocal unresolved
        name = m["braced"] or m["bare"]
        if name not in args:
            unresolved = True
            return ""
        return args[name]

    out = VARIABLE.sub(sub, ref)
    return None if unresolved else out


def image_refs(cmd: str, rest: str, stages: set[str]) -> list[tuple[str, str]]:
    """External image refs a FROM/COPY instruction pulls, as (label, ref); records stages."""
    if cmd == "FROM":
        tokens = [t for t in rest.split() if not t.startswith("--")]
        if not tokens:
            return []
        image = tokens[0]
        refs = [] if image.lower() in {"scratch", *stages} else [("FROM ", image)]
        if len(tokens) >= 3 and tokens[1].lower() == "as":
            stages.add(
                tokens[2].lower()
            )  # after the check: only earlier stages are aliases
        return refs
    if cmd in {"COPY", "RUN"}:
        pattern, label = (
            (COPY_FROM, "--from=") if cmd == "COPY" else (MOUNT_FROM, "from=")
        )
        refs = [m["ref"] for m in pattern.finditer(rest)]
        return [(label, r) for r in refs if not r.isdigit() and r.lower() not in stages]
    return []


def image_problem(ref: str, args: dict[str, str]) -> str | None:
    resolved = resolve(ref, args)
    if resolved is None:
        return (
            "build arg has no pinned default; "
            "give the ARG a digest-pinned default or add # pin-exempt"
        )
    return mutable_image(resolved)


def check_dockerfile(path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    stages: set[str] = set()
    args: dict[str, str] = {}
    for lineno, exempt, text in instructions(lines):
        m = INSTRUCTION.match(text)
        if not m:
            continue
        cmd, rest = m["cmd"].upper(), m["args"]
        if cmd == "ARG":
            for token in rest.split():
                if arg := ARG_DEFAULT.match(token):
                    args[arg["name"]] = arg["value"].strip("\"'")
        for label, ref in image_refs(cmd, rest, stages):
            if not exempt and (why := image_problem(ref, args)):
                errors.append(f"{path}:{lineno}: {label}{ref}: {why}")
    return errors


def uses_problem(ref: str) -> str | None:
    if ref.startswith("./"):
        return None
    if ref.startswith("docker://"):
        return mutable_image(ref.removeprefix("docker://"))
    return None if COMMIT_SHA.search(ref) else "pin to a 40-character commit sha"


def container_problem(key: str, ref: str, is_action_metadata: bool) -> str | None:
    if is_action_metadata and key == "image":
        # runs.image in action metadata: a local Dockerfile path, or docker://<image>
        if not ref.startswith("docker://"):
            return None
        ref = ref.removeprefix("docker://")
    if "${{" in ref:
        return (
            "expression cannot be verified as pinned; "
            "use a digest-pinned literal or add # pin-exempt"
        )
    return mutable_image(ref)


def check_actions_file(path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    is_action_metadata = PurePosixPath(path).stem == "action"
    for lineno, line in enumerate(lines, 1):
        if m := USES.match(line):
            ref = m["ref"].strip("\"'")
            if why := uses_problem(ref):
                errors.append(f"{path}:{lineno}: uses {ref}: {why}")
        elif m := LATEST_INPUT.match(line):
            errors.append(
                f"{path}:{lineno}: {m['key']}: latest is mutable; name an exact version"
            )
        elif m := IMAGE.match(line):
            if lineno > 1 and EXEMPT.match(lines[lineno - 2]):
                continue
            key, ref = m["key"], m["ref"].strip("\"'")
            if why := container_problem(key, ref, is_action_metadata):
                errors.append(f"{path}:{lineno}: {key} {ref}: {why}")
    return errors


def main() -> int:
    errors: list[str] = []
    for path in list_files():
        if is_dockerfile(path):
            checker = check_dockerfile
        elif is_actions_file(path):
            checker = check_actions_file
        else:
            continue
        if not os.path.exists(path):
            continue  # tracked but deleted in the working tree
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(
                f"{path}: could not be read ({exc}); cannot verify its references"
            )
            continue
        errors.extend(checker(path, lines))

    if errors:
        print(
            "Mutable references found (pin images by digest, actions by commit sha):",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
