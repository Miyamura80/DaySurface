"""Tests for CLI global state."""

from src.cli.state import (
    OutputFormat,
    Verbosity,
    dry_run,
    is_debug,
    is_dry_run,
    is_quiet,
    is_verbose,
    output_format,
    verbosity,
)
from tests.test_template import TestTemplate


class TestVerbosity(TestTemplate):
    def test_default_is_normal(self):
        token = verbosity.set(Verbosity.NORMAL)
        try:
            assert verbosity.get() == Verbosity.NORMAL
        finally:
            verbosity.reset(token)

    def test_is_verbose_when_verbose(self):
        token = verbosity.set(Verbosity.VERBOSE)
        try:
            assert is_verbose()
            assert not is_quiet()
            assert not is_debug()
        finally:
            verbosity.reset(token)

    def test_is_quiet_when_quiet(self):
        token = verbosity.set(Verbosity.QUIET)
        try:
            assert is_quiet()
            assert not is_verbose()
        finally:
            verbosity.reset(token)

    def test_is_debug_when_debug(self):
        token = verbosity.set(Verbosity.DEBUG)
        try:
            assert is_debug()
            assert is_verbose()
        finally:
            verbosity.reset(token)


class TestOutputFormat(TestTemplate):
    def test_default_is_table(self):
        token = output_format.set(OutputFormat.TABLE)
        try:
            assert output_format.get() == OutputFormat.TABLE
        finally:
            output_format.reset(token)


class TestDryRun(TestTemplate):
    def test_default_is_false(self):
        token = dry_run.set(False)
        try:
            assert not is_dry_run()
        finally:
            dry_run.reset(token)

    def test_set_and_read(self):
        token = dry_run.set(True)
        try:
            assert is_dry_run()
        finally:
            dry_run.reset(token)
