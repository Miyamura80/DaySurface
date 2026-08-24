"""Lazy database engine initialisation.

The engine is created on first use so that CLI / MCP transports work
without a database configured.
"""

import threading
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from common import global_config

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_init_lock = threading.Lock()


CONNECT_TIMEOUT_SECONDS = 5


def _connect_args(uri: str) -> dict[str, object]:
    """Driver-specific connect args, principally a bounded connect timeout.

    Without one, a database host that stops answering SYN (a failover, a
    security group change) leaves every connect blocked on OS TCP retries -
    ~130s on Linux. Anything holding a worker while that happens is stuck for
    the duration, so an unreachable database degrades from "queries fail" to
    "the process stops serving". ``pool_pre_ping`` does not help: the ping is
    itself a connect.

    Only libpq-backed URIs get it; SQLite (used by the test suite) has no such
    option and would reject the kwarg.
    """
    if uri.startswith(("postgresql", "postgres:")):
        return {"connect_args": {"connect_timeout": CONNECT_TIMEOUT_SECONDS}}
    return {}


def _init_engine() -> Engine:
    """Create the engine from ``global_config.BACKEND_DB_URI`` (thread-safe).

    Double-checked locking so concurrent first-callers (e.g. two requests
    offloaded to the threadpool on a fresh process) initialise exactly once.
    ``_engine`` is published *last* - after ``_SessionLocal`` - so any thread
    that sees ``_engine`` set is guaranteed to also see a non-None
    ``_SessionLocal`` (which ``use_db_session`` asserts), closing the window
    where one request could 500 mid-initialisation.
    """
    global _engine, _SessionLocal  # noqa: PLW0603

    if _engine is not None:
        return _engine

    with _init_lock:
        if _engine is not None:
            return _engine

        uri = global_config.BACKEND_DB_URI
        if not uri:
            raise RuntimeError(
                "BACKEND_DB_URI is not configured. "
                "Set it in your .env file to use database features."
            )

        engine = create_engine(uri, pool_pre_ping=True, **_connect_args(uri))
        _SessionLocal = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        _engine = engine
        return _engine


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    _init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    except Exception:  # noqa: BLE001
        # Session boundary: any exception escaping the request handler must
        # roll back the transaction before re-raising.
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def use_db_session() -> Generator[Session, None, None]:
    """Context-manager wrapper for non-FastAPI code."""
    _init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    except Exception:  # noqa: BLE001
        # Session boundary: any exception escaping the with-block must roll
        # back the transaction before re-raising.
        session.rollback()
        raise
    finally:
        session.close()
