"""Unit tests for the stdin value-resolution helpers."""

import io
import sys

import pytest
import typer

from src.utils.stdin import read_stdin, resolve_value
from tests.test_template import TestTemplate


class _FakeStdin(io.StringIO):
    """StringIO with a controllable isatty()."""

    def __init__(self, data: str, tty: bool = False):
        super().__init__(data)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class TestResolveValue(TestTemplate):
    def test_plain_value_passthrough(self):
        assert resolve_value("foo") == "foo"

    def test_none_passthrough(self):
        assert resolve_value(None) is None

    def test_stdin_flag_reads_value(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("piped\n"))
        assert resolve_value(None, use_stdin=True) == "piped"

    def test_dash_sentinel_reads_value(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("piped\n"))
        assert resolve_value("-") == "piped"

    def test_empty_stdin_resolves_to_none(self, monkeypatch):
        # Empty pipe must be treated as "missing", not an empty string.
        monkeypatch.setattr(sys, "stdin", _FakeStdin(""))
        assert resolve_value(None, use_stdin=True) is None

    def test_crlf_terminator_stripped(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("val\r\n"))
        assert resolve_value(None, use_stdin=True) == "val"

    def test_stdin_overrides_positional_value(self, monkeypatch):
        # When --stdin is set, the piped value wins over any positional.
        monkeypatch.setattr(sys, "stdin", _FakeStdin("fromstdin\n"))
        assert resolve_value("positional", use_stdin=True) == "fromstdin"


class TestReadStdin(TestTemplate):
    def test_reads_and_strips(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeStdin("hello\n"))
        assert read_stdin() == "hello"

    def test_tty_with_nothing_piped_fails_fast(self, monkeypatch):
        # Must not block on a read that never returns; exit 2 instead.
        monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
        with pytest.raises(typer.Exit) as exc:
            read_stdin()
        assert exc.value.exit_code == 2
