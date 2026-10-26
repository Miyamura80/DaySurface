"""Tests for scripts/check_immutable_refs.py (`check_dockerfile`, `check_actions_file`)."""

from __future__ import annotations

import pytest

import scripts.check_immutable_refs as c

DIGEST = "@sha256:" + "a" * 64
SHA = "@" + "b" * 40


@pytest.mark.parametrize(
    "lines",
    [
        ["FROM node:24-slim"],
        ["FROM node"],
        ["FROM node:latest"],
        ["FROM --platform=linux/amd64 python:3.12-slim AS backend"],
        ["FROM node:24-slim@sha256:short"],
        ["COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv"],
        ["COPY --from=busybox /bin/sh /sh"],
        ["# pin-exempt:", "FROM node:24-slim"],
        ["# pin-exempt: local", "RUN true", "FROM node:24-slim"],
        # Stage aliases only cover earlier stages, never the base being declared.
        ["FROM ubuntu AS ubuntu"],
        # Build args resolve from their default, which must itself be pinned.
        ["ARG BASE", "FROM $BASE"],
        ["ARG BASE=ubuntu:latest", "FROM ${BASE}"],
        ["ARG UV=ghcr.io/astral-sh/uv:0.13.5", "COPY --from=$UV /uv /uv"],
        # FROM sees only global ARGs: a pinned redeclaration inside a stage does not apply.
        [
            "ARG BASE=ubuntu:latest",
            f"FROM node{DIGEST}",
            f"ARG BASE=ubuntu{DIGEST}",
            "FROM $BASE",
        ],
        # A stage sees a global ARG only by redeclaring it.
        [f"ARG UV=uv:0.13.5{DIGEST}", f"FROM node{DIGEST}", "COPY --from=$UV /uv /uv"],
        # ...and an inherited mutable ARG is still checked.
        [
            f"FROM node{DIGEST} AS base",
            "ARG UV=uv:latest",
            "FROM base",
            "RUN --mount=type=bind,from=$UV,target=/uv true",
        ],
        # An empty default replaces the earlier pinned one.
        [f"ARG BASE=node{DIGEST}", "ARG BASE=", "FROM $BASE"],
        # Continuation lines are joined before parsing.
        ["FROM \\", "    --platform=linux/amd64 \\", "    node:24-slim AS app"],
        ["copy --from=busybox /bin/sh /sh"],
        ["RUN --mount=type=bind,from=busybox:1.36,target=/b true"],
        # An exemption covers only the instruction directly after it.
        ["# pin-exempt: local", "", "FROM node:24-slim"],
        # Docker skips blank lines inside a continuation, so the guard must too.
        ["FROM \\", "", "    node:24-slim AS app"],
        # A `# escape=` directive changes the continuation character.
        ["# escape=`", "RUN --mount=type=bind,`", "from=busybox:1.36,target=/b true"],
        # Docker joins continuations without adding a space.
        ["RUN --mount=type=bind,\\", "from=busybox:1.36,target=/b true"],
    ],
)
def test_dockerfile_flags_mutable(lines: list[str]) -> None:
    assert c.check_dockerfile("Dockerfile", lines)


@pytest.mark.parametrize(
    "lines",
    [
        [f"FROM node:24.11.1-slim{DIGEST} AS frontend", "COPY --from=frontend /a /b"],
        [f"FROM --platform=linux/amd64 python:3.12-slim{DIGEST} AS backend"],
        ["FROM scratch"],
        [f"FROM node:24-alpine{DIGEST} AS base", "FROM base AS runner"],
        [f"COPY --from=ghcr.io/astral-sh/uv:0.13.4{DIGEST} /uv /usr/local/bin/uv"],
        [f"ARG BASE=node:24-slim{DIGEST}", "FROM $BASE"],
        [
            f"ARG UV=uv:0.13.5{DIGEST}",
            f"FROM node{DIGEST}",
            "ARG UV",
            "COPY --from=$UV /uv /uv",
        ],
        [f"FROM node{DIGEST}", f"ARG UV=uv:0.13.5{DIGEST}", "COPY --from=$UV /uv /uv"],
        # A stage built on an earlier stage inherits that stage's ARGs.
        [
            f"FROM node{DIGEST} AS base",
            f"ARG UV=uv:0.13.5{DIGEST}",
            "FROM base AS child",
            "RUN --mount=type=bind,from=$UV,target=/uv true",
        ],
        ["FROM \\", f"    node:24-slim{DIGEST} AS app", "COPY --from=app /a /b"],
        [f"FROM node{DIGEST}", "COPY --from=0 /a /b"],
        # --from= outside a COPY instruction is not an image reference.
        [f"FROM node{DIGEST}", "# COPY --from=busybox", "RUN echo --from=busybox"],
        [
            f"FROM node{DIGEST} AS deps",
            "RUN --mount=type=bind,from=deps,target=/d true",
        ],
        [f"RUN --mount=type=cache,target=/root/.npm --mount=from=busybox{DIGEST} true"],
        [
            "# pin-exempt: built locally from docker/Dockerfile",
            "FROM desktest-desktop:latest",
        ],
    ],
)
def test_dockerfile_allows_pinned(lines: list[str]) -> None:
    assert c.check_dockerfile("Dockerfile", lines) == []


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@v6",
        "        uses: dtolnay/rust-toolchain@stable",
        "    uses: org/repo/.github/workflows/ci.yml@main",
        "      - uses: actions/checkout@" + "b" * 39,
        "      - uses: docker://alpine:latest",
        "    image: postgres:16",
        "    image: nicolaka/netshoot",
        "    container: node:latest",
        '      - "uses": actions/checkout@v6',
        "      - uses: actions/checkout@main@" + "b" * 40,
        "          bun-version: latest",
        "          node-version: 'latest' # newest",
        "    image: ${{ matrix.image }}",
        # Space before the colon and flow-style mappings are valid YAML.
        "      - uses : actions/checkout@v6",
        "      - uses: checkout@" + "b" * 40,
        # Every match on a flow-style line is checked, not only the first.
        f"    steps: [{{ uses: actions/checkout{SHA} }}, {{ uses: actions/cache@v4 }}]",
        f"    services: {{ a: {{ image: pg:16{DIGEST} }}, b: {{ image: redis:7 }} }}",
        "      - { name: co, uses: actions/checkout@v6 }",
        "    container: { image: node:latest }",
        # An anchor or tag before a flow collection does not hide what is inside it.
        "    steps: &pinned [{ uses: actions/checkout@v4 }]",
        "      - !!map { uses: actions/checkout@v4 }",
        "      - &step{ uses: actions/checkout@v4 }",
    ],
)
def test_actions_flags_mutable(line: str) -> None:
    assert c.check_actions_file("ci.yaml", [line])


@pytest.mark.parametrize(
    "line",
    [
        f"      - uses: actions/checkout{SHA} # v6.0.3",
        f'      - uses: "actions/checkout{SHA}"',
        f"    uses: org/repo/.github/workflows/ci.yml{SHA}",
        "      - uses: ./.github/actions/setup",
        f"      - uses: docker://alpine:3.22{DIGEST}",
        f"    image: postgres:16.4{DIGEST}",
        f"    container: node:24-slim{DIGEST}",
        "          bun-version: 1.4.3",
        "          tag: latest",
        "    container:",
        f"      - {{ uses: actions/checkout{SHA} }}",
        f"    container: {{ image: node:24-slim{DIGEST} }}",
        "      - run: 'echo \"uses: actions/checkout@v6\"'",
        # Braces and commas inside a run: string are not flow-style YAML keys.
        "      - run: 'echo \"text, uses: actions/checkout@v4\"'",
        '      - run: "echo \'{\\"uses\\": \\"not-an-action\\"}\'"',
        "        run: 'echo { image: alpine }'",
    ],
)
def test_actions_allows_pinned(line: str) -> None:
    assert c.check_actions_file("ci.yaml", [line]) == []


@pytest.mark.parametrize(
    "lines",
    [
        ["          bun-version: >-", "            latest"],
        ["          bun-version: |", "            latest"],
        ["          node-version:", "            'latest'"],
        ["          bun-version:", "            latest  # newest"],
        [
            "          bun-version:",
            "            # newest release",
            "            latest",
        ],
        ["        with: { node-version:", "          latest }"],
        ["        with: { cache: npm, node-version:", "          'latest', other: x }"],
    ],
)
def test_actions_flags_multiline_latest(lines: list[str]) -> None:
    assert len(c.check_actions_file("ci.yml", lines)) == 1


@pytest.mark.parametrize(
    "lines",
    [
        ["          bun-version: >-", "            1.4.3"],
        ["          with:", "            bun-version: 1.4.3"],
        ["          node-version:", "          other: latest"],
    ],
)
def test_actions_allows_multiline_exact_version(lines: list[str]) -> None:
    assert c.check_actions_file("ci.yml", lines) == []


@pytest.mark.parametrize(
    "lines",
    [
        # A ref on the line below its key is still a ref.
        ["      - uses:", "          actions/checkout@v6"],
        ["      - uses: >-", "          actions/checkout@v6"],
        ["    container:", "      node:latest"],
        ["      db:", "        image:", "          'postgres:16'"],
    ],
)
def test_actions_flags_ref_on_next_line(lines: list[str]) -> None:
    assert c.check_actions_file("ci.yml", lines) != []


@pytest.mark.parametrize(
    "lines",
    [
        ["      - uses:", f"          actions/checkout{SHA}"],
        ["      - uses: >-", f"          actions/checkout{SHA}"],
        ["    image: |", f"      postgres:16{DIGEST}"],
        # `container:` holding a mapping, and `image:` holding a pinned scalar.
        ["    container:", f"      image: node:24{DIGEST}", "      options: --cpus 1"],
        ["    container:", "      # nested below", f"      image: node:24{DIGEST}"],
        ["    image:", f"      postgres:16{DIGEST}"],
        [
            "    # pin-exempt: built in the previous job",
            "    container:",
            "      local:dev",
        ],
    ],
)
def test_actions_allows_ref_on_next_line(lines: list[str]) -> None:
    assert c.check_actions_file("ci.yml", lines) == []


@pytest.mark.parametrize(
    "lines",
    [
        # A flow mapping split across lines keeps flow parsing on its continuation lines.
        ["      - { name: co,", "          id: x, uses: actions/checkout@v6 }"],
        ["    services: {", "      db: { image: pg:16 }, cache: { image: redis:7 } }"],
    ],
)
def test_actions_flags_multiline_flow(lines: list[str]) -> None:
    assert c.check_actions_file("ci.yml", lines) != []


def test_actions_flow_depth_ends_with_collection() -> None:
    lines = [
        f"      - {{ name: co, uses: actions/checkout{SHA} }}",
        "      - run: \"echo 'a, uses: b'\"",
        '        name: "x { y"',
        "      - run: 'echo done, uses: nothing'",
    ]
    assert c.check_actions_file("ci.yml", lines) == []


def test_workflow_named_action_is_not_action_metadata() -> None:
    lines = ["    services:", "      db:", "        image: someorg/dockerfile"]
    assert c.check_actions_file(".github/workflows/action.yml", lines) != []
    assert (
        c.check_actions_file("tools/build/action.yml", ["    image: Dockerfile"]) == []
    )


def test_actions_exempt_covers_images_only() -> None:
    uses = "      - uses: actions/checkout@v6"
    assert c.check_actions_file("ci.yaml", ["      # pin-exempt: reason", uses])
    image = "    image: postgres:16"
    assert c.check_actions_file("ci.yaml", ["    # pin-exempt: reason", image]) == []
    assert c.check_actions_file("ci.yaml", ["    # pin-exempt:", image])
    literal = "    image: postgres:latest # ${{ not an expression }}"
    assert "latest" in c.check_actions_file("ci.yaml", [literal])[0]
    expr = "    image: ${{ matrix.image }}"
    assert (
        c.check_actions_file(
            "ci.yaml", ["    # pin-exempt: matrix values pinned", expr]
        )
        == []
    )


def test_action_metadata_local_dockerfile() -> None:
    assert c.check_actions_file("action.yml", ["  image: Dockerfile"]) == []
    assert c.check_actions_file("action.yml", ["  image: docker://alpine:3.22"])
    assert c.check_actions_file("action.yml", ["  image: ./docker/Dockerfile"]) == []
    assert c.check_actions_file("action.yml", ["  image: alpine:latest"])
    assert c.check_actions_file("action.yml", ["  image: dockerfile:latest"])
    assert c.check_actions_file("action.yml", ["  image: alpine:latestDockerfile"])
    assert c.check_actions_file("action.yml", ["  image: build/app.Dockerfile"]) == []
    assert c.check_actions_file("action.yml", [f"  image: alpine:3.22{DIGEST}"]) == []


@pytest.mark.parametrize(
    ("path", "dockerfile", "actions"),
    [
        ("Dockerfile", True, False),
        ("agent-sniffer/containers/Dockerfile.nfstream", True, False),
        ("Dockerfile.docs.dockerignore", False, False),
        (".github/workflows/ci.yaml", False, True),
        (".github/actions/setup/action.yml", False, True),
        (".github/dependabot.yml", False, False),
        ("docker-compose.yml", False, False),
        ("docker/app.dockerfile", True, False),
        ("tools/my-action/action.yaml", False, True),
    ],
)
def test_file_selection(path: str, dockerfile: bool, actions: bool) -> None:
    assert c.is_dockerfile(path) is dockerfile
    assert c.is_actions_file(path) is actions
