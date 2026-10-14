import asyncio
import inspect
import json
import logging
import os
import re
import sys
import threading
import traceback

import scrubadub
from human_id import generate_id
from loguru import logger

from common import global_config
from src.utils.context import session_id

_logging_initialized = False
_logging_lock = threading.Lock()


class _LogScrubber:
    """
    Optimized single-pass log scrubber.
    Uses scrubadub for general PII and a compiled multi-pattern regex for secrets.
    """

    def __init__(self):
        config = global_config.logging.redaction
        self.enabled = config.enabled
        self.use_default_pii = config.use_default_pii
        self.patterns = config.patterns

        # Initialize scrubadub
        self.scrubber = None
        if self.enabled and self.use_default_pii:
            self.scrubber = scrubadub.Scrubber()
            # Remove default FilenameDetector if it's too aggressive, but usually it's fine
            # We can customize detectors here if needed

        # Compile custom patterns into a single-pass regex
        self.combined_regex = None
        self.placeholder_map = {}

        if self.enabled and self.patterns:
            regex_parts = []
            for i, p in enumerate(self.patterns):
                group_name = f"p{i}"
                regex_parts.append(f"(?P<{group_name}>{p.regex})")
                self.placeholder_map[group_name] = p.placeholder

            self.combined_regex = re.compile("|".join(regex_parts))

    def _redact_callback(self, match):
        """Callback for re.sub to return the correct placeholder for the matched group."""
        group_name = match.lastgroup
        return self.placeholder_map.get(group_name, "[REDACTED]")

    def scrub(self, text: str) -> str:
        """Scrub sensitive data from text in a single pass."""
        if not self.enabled or not text:
            return text

        # 1. Scrub general PII using scrubadub
        if self.scrubber:
            text = self.scrubber.clean(text)

        # 2. Scrub custom secrets (single pass)
        if self.combined_regex:
            text = self.combined_regex.sub(self._redact_callback, text)

        return text


# Initialize the singleton scrubber
_SCRUBBER = _LogScrubber()


def scrub_sensitive_data(record):
    """
    Patch function to scrub sensitive data from the log record.
    Modifies record["message"] and record["exception"] in place.
    """
    if not _SCRUBBER.enabled:
        return

    # Scrub main message
    record["message"] = _SCRUBBER.scrub(record["message"])

    # Scrub exception if present
    exception = record.get("exception")
    if exception:
        type_, value, tb = exception
        value_str = str(value)
        scrubbed_value_str = _SCRUBBER.scrub(value_str)

        if scrubbed_value_str != value_str:
            # Modify args in-place to preserve exception type and metadata
            # Most exceptions store their message in args[0]
            try:
                if value.args:
                    scrubbed_args = tuple(
                        _SCRUBBER.scrub(str(arg)) if isinstance(arg, str) else arg
                        for arg in value.args
                    )
                    value.args = scrubbed_args
            except (AttributeError, TypeError):
                # If args modification fails, leave original exception unchanged
                # This is safer than creating a mismatched exception type
                pass

    # Scrub extra context if present
    extra = record.get("extra")
    if extra:
        for key, val in extra.items():
            if isinstance(val, str):
                extra[key] = _SCRUBBER.scrub(val)


def _should_show_location(level: str) -> bool:
    """Determine if location should be shown for given log level"""
    level = level.lower()
    config = global_config.logging.format.location

    if not config.enabled:
        return False

    level_map = {
        "info": config.show_for_info,
        "debug": config.show_for_debug,
        "warning": config.show_for_warning,
        "error": config.show_for_error,
    }

    return level_map.get(level, True)  # Default to True for unknown levels


def _get_task_name() -> str:
    """Get the current asyncio task name if it exists"""
    try:
        task = asyncio.current_task()
        if task:
            # Get task name, fallback to a shorter task ID format
            name = getattr(task, "name", None)
            if name:
                return name
            return "main"  # Default to 'main' if no name set
        return "main"
    except RuntimeError:
        # If called from outside asyncio event loop
        return "main"


def _get_replica_id() -> str:
    """Get the current Railway replica ID and transform it into a simple numeric index"""
    raw_id = os.getenv("RAILWAY_REPLICA_ID")
    if not raw_id:
        return "local"

    # Extract the last few characters of the ID and convert to an integer
    # This will give us a consistent number for each replica
    try:
        # Take last 4 chars of ID and convert to int, then mod with 100 to keep numbers small
        numeric_id = int(raw_id[-4:], 16) % 100
        return f"r{numeric_id}"  # prefix with 'r' to indicate replica
    except (ValueError, TypeError):
        return raw_id  # fallback to original ID if conversion fails


def _get_session_color(session_id: str) -> str:
    """Get a consistent color for a given session ID"""
    if session_id == "---":
        return "white"

    # List of distinct colors that work well in terminals
    colors = ["green", "yellow", "blue", "magenta", "cyan", "red"]

    # Convert session ID to a consistent numeric value
    # Take last 8 chars to limit the size of the number
    numeric_id = sum(ord(c) for c in session_id[-8:])
    color_index = numeric_id % len(colors)

    return colors[color_index]


def _build_format_string(record: dict) -> str:
    """Build format string dynamically based on log level"""
    format_parts = ["<level>{level: <6}</level>"]

    if global_config.logging.format.show_time:
        format_parts.append("{time:HH:mm:ss}")

    if global_config.logging.format.show_session_id:
        session_color = _get_session_color(record["extra"]["session_id"])
        format_parts.append(f"<{session_color}>{{extra[session_id]}}</{session_color}>")

    # Add replica ID to format string instead of task name
    format_parts.append("<magenta>{extra[replica_id]}</magenta>")

    # Build the location part of the format string if needed for this level
    if _should_show_location(record["level"].name):
        location_parts = []
        config = global_config.logging.format.location

        if config.show_file:
            location_parts.append("<cyan>{file.name}</cyan>")
        if config.show_function:
            location_parts.append("<cyan>{function}</cyan>")
        if config.show_line:
            location_parts.append("<cyan>{line}</cyan>")

        if location_parts:
            format_parts.append(":".join(location_parts))

    format_parts.append("<level>{message}</level>{exception}")
    return " | ".join(format_parts) + "\n"  # Added newline here


# Railway reads a single-line JSON object's ``level`` as the line's severity and
# its other fields as filterable attributes. A plain line gets no level, so
# Railway files everything on stderr as "error", INFO included.
_RAILWAY_LEVELS = {
    "TRACE": "debug",
    "DEBUG": "debug",
    "INFO": "info",
    "SUCCESS": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}


def _json_line(record) -> str:
    """Render a loguru record as one JSON line for Railway's log explorer.

    A traceback is rendered with the stdlib formatter, which never prints frame
    locals, and rides in ``message`` so it shows with the line it belongs to.
    """
    message = record["message"].rstrip()
    exception = record["exception"]
    if exception:
        message += (
            "\n"
            + "".join(
                traceback.format_exception(
                    exception.type, exception.value, exception.traceback
                )
            ).rstrip()
        )
    extra = record["extra"]
    return json.dumps(
        {
            "level": _RAILWAY_LEVELS.get(record["level"].name, "info"),
            "message": message,
            "logger": extra.get("stdlib_logger") or record["name"],
            "location": f"{record['file'].name}:{record['function']}:{record['line']}",
            "session_id": extra.get("session_id"),
            "replica": extra.get("replica_id"),
        },
        default=str,
    )


def _json_format(record) -> str:
    # Build the line here, in the logging thread: with enqueue=True the record is
    # pickled to the writer thread, and pickling drops the traceback.
    record["extra"]["json_line"] = _json_line(record)
    return "{extra[json_line]}\n"


class _InterceptHandler(logging.Handler):
    """Forward stdlib ``logging`` records (uvicorn, mcp, warnings) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Step out of logging's own frames so loguru attributes the real caller.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.bind(stdlib_logger=record.name).opt(
            depth=depth, exception=record.exc_info
        ).log(level, record.getMessage())


def _route_stdlib_logging() -> None:
    """Send every stdlib logger through loguru so all output shares one format.

    uvicorn installs its own stream handlers before it imports the app; clearing
    them here (the app import is the earliest point we run) lets its records
    propagate to the root intercept instead. Logger-level filters, like the
    access-log redactor in ``api_server.server``, still run first. With a root
    handler in place, FastMCP's ``logging.basicConfig`` call becomes a no-op.
    """
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
    logging.captureWarnings(True)


def _diagnose_enabled() -> bool:
    """Whether loguru's ``diagnose``/``backtrace`` should be on for this env.

    ``diagnose`` renders the repr of every stack-frame local inline in
    exception tracebacks, which would dump a ``refresh_token`` / ``client_secret``
    / ``client_id`` held in a frame straight into the logs. It must therefore be
    enabled only in development. ``global_config.is_dev`` is the single owner of
    that predicate and fails secure (unset / ``staging`` / ``prod`` / a typo all
    read as production), so this returns ``False`` everywhere except local/dev.

    Factored out of ``setup_logging`` so the gating decision can be unit-tested
    without intercepting the ``logger.add`` call.
    """
    return global_config.is_dev


def _should_log_level(level: str, overrides: dict | None = None) -> bool:
    """Determine if this log level should be shown based on config and overrides"""
    level = level.lower()

    if overrides is None:
        overrides = {}

    # Check overrides first if they exist
    if overrides and level in overrides:
        return overrides[level]

    # Fall back to global config
    try:
        return getattr(global_config.logging.levels, level)
    except AttributeError:
        return True


def setup_logging(  # noqa: C901
    *, debug=None, info=None, warning=None, error=None, critical=None
):
    """Setup centralized logging configuration with optional level overrides

    Args:
        debug (bool, optional): Override global debug log level
        info (bool, optional): Override global info log level
        warning (bool, optional): Override global warning log level
        error (bool, optional): Override global error log level
        critical (bool, optional): Override global critical log level
    """
    global _logging_initialized, logger

    if _logging_initialized:
        return

    with _logging_lock:
        if _logging_initialized:
            return

        # Remove any existing handlers
        logger.remove()

        # Configure global patcher for log scrubbing
        logger.configure(patcher=scrub_sensitive_data)

        # Initialize session_id if not already set
        if session_id.get() is None:
            session_id.set(generate_id())

        # Build overrides dict from provided arguments
        overrides = {}
        if debug is not None:
            overrides["debug"] = debug
        if info is not None:
            overrides["info"] = info
        if warning is not None:
            overrides["warning"] = warning
        if error is not None:
            overrides["error"] = error
        if critical is not None:
            overrides["critical"] = critical

        # Add session_id, replica ID, and level filtering to all log records
        def log_filter(record):
            # Add session ID and replica ID
            if "extra" not in record:
                record["extra"] = {}
            record["extra"]["session_id"] = session_id.get() or "---"
            record["extra"]["replica_id"] = _get_replica_id()

            # Check if this level should be logged using overrides
            return _should_log_level(record["level"].name, overrides)

        # Add our standardized handler with dynamic format and filter.
        #
        # diagnose renders the repr of every stack-frame local inline in
        # exception tracebacks; a frame holding a refresh_token / client_secret
        # / client_id would then be dumped straight into the logs. Gate it on
        # the dev environment so it is False in production and staging and only
        # True in local/dev. global_config.is_dev fails secure (anything that is
        # not local/dev is treated as production), so this can never relax open.
        #
        # backtrace only expands the call stack (function names / lines), not
        # local values, so it is lower-risk; we gate it the same way anyway to
        # keep production tracebacks compact and to avoid any future value
        # rendering it might grow.
        diagnose_enabled = _diagnose_enabled()
        if global_config.logging.structured:
            logger.add(
                sys.stdout,
                format=_json_format,
                colorize=False,
                enqueue=True,
                backtrace=False,
                diagnose=False,
                catch=True,
                filter=log_filter,
            )
            _route_stdlib_logging()
        else:
            logger.add(
                sys.stderr,
                format=lambda record: _build_format_string(record),
                colorize=True,
                enqueue=True,
                backtrace=diagnose_enabled,
                diagnose=diagnose_enabled,
                catch=True,
                filter=log_filter,
            )

        _logging_initialized = True
