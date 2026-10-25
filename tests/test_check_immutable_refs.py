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
        ["ARG BASE", "FROM $BASE"],
        [f"FROM node{DIGEST}", "COPY --from=0 /a /b"],
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
        "    image: ${{ matrix.image }}",
    ],
)
def test_actions_allows_pinned(line: str) -> None:
    assert c.check_actions_file("ci.yaml", [line]) == []


def test_actions_exempt_needs_reason() -> None:
    uses = "      - uses: actions/checkout@v6"
    assert c.check_actions_file("ci.yaml", ["      # pin-exempt: reason", uses]) == []
    assert c.check_actions_file("ci.yaml", ["      # pin-exempt:", uses])


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
    ],
)
def test_file_selection(path: str, dockerfile: bool, actions: bool) -> None:
    assert c.is_dockerfile(path) is dockerfile
    assert c.is_actions_file(path) is actions
