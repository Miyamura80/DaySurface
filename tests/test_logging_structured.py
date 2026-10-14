import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from loguru import logger

from src.utils.logging_config import (
    _InterceptHandler,
    _json_format,
    _route_stdlib_logging,
    scrub_sensitive_data,
)
from tests.test_template import TestTemplate


@contextmanager
def _captured() -> Iterator[list[str]]:
    """Collect lines from a JSON sink like production's, minus enqueue."""
    lines: list[str] = []
    sink_id = logger.add(
        lines.append, format=_json_format, colorize=False, diagnose=False
    )
    try:
        yield lines
    finally:
        logger.remove(sink_id)


def _records(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines]


class TestStructuredLogging(TestTemplate):
    def test_levels_map_to_railway_severities(self):
        with _captured() as lines:
            logger.info("i")
            logger.warning("w")
            logger.error("e")
            logger.critical("c")
        assert [r["level"] for r in _records(lines)] == [
            "info",
            "warn",
            "error",
            "error",
        ]

    def test_one_line_per_record_with_attributes(self):
        with _captured() as lines:
            logger.info("hello {not_a_field}")
        (line,) = lines
        assert line.endswith("\n") and line.count("\n") == 1
        record = json.loads(line)
        assert record["message"] == "hello {not_a_field}"
        assert {"level", "logger", "session_id", "replica"} <= record.keys()
        assert record["location"].startswith("test_logging_structured.py:")

    def test_traceback_rides_in_the_message(self):
        with _captured() as lines:
            try:
                raise ValueError("bad input")
            except ValueError:
                logger.exception("failed")
        (record,) = _records(lines)
        assert record["level"] == "error"
        assert record["message"].startswith(
            "failed\nTraceback (most recent call last):"
        )
        assert "ValueError: bad input" in record["message"]

    def test_scrubbed_before_rendering(self):
        with _captured() as lines:
            logger.patch(scrub_sensitive_data).info("mail test@example.com")
        assert "test@example.com" not in lines[0]

    def test_stdlib_records_keep_their_logger_and_caller(self):
        stdlib = logging.getLogger("uvicorn.error.test")
        stdlib.propagate = False
        stdlib.setLevel(logging.INFO)
        stdlib.addHandler(_InterceptHandler())
        try:
            with _captured() as lines:
                stdlib.info("Started server process")
                stdlib.warning("disk %s", "low")
        finally:
            stdlib.handlers.clear()
        info, warn = _records(lines)
        assert info["logger"] == "uvicorn.error.test"
        assert info["level"] == "info"
        assert warn == warn | {"level": "warn", "message": "disk low"}
        assert info["location"].startswith("test_logging_structured.py:")

    def test_root_level_follows_the_debug_setting(self):
        for debug, level in ((False, logging.INFO), (True, logging.DEBUG)):
            with (
                patch("logging.basicConfig") as basic_config,
                patch("logging.captureWarnings"),
            ):
                _route_stdlib_logging(debug=debug)
            assert basic_config.call_args.kwargs["level"] == level
