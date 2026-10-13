"""Authenticated FastAPI server that exposes the service registry over HTTP."""

from src.utils.logging_config import setup_logging

# Configure logging before the server module and its dependency graph import.
# Production loads ``api_server.server:app`` via the uvicorn CLI, which imports
# this package first, so this is the earliest point the app controls. Calling
# setup_logging() here installs the sensitive-data scrubber and gates loguru's
# diagnose/backtrace off in production (DEV_ENV=prod -> is_dev False) before
# build_mcp_server(), the rate limiter, and feature flags log at import time.
setup_logging()

from api_server.server import main  # noqa: E402 - must import after logging setup

__all__ = ["main"]
