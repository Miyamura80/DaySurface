"""Lazy database engine initialisation.

The engine is created on first use so that CLI / MCP transports work
without a database configured.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from common import global_config

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _init_engine() -> Engine:
    """Create the engine from ``global_config.BACKEND_DB_URI``."""
    global _engine, _SessionLocal  # noqa: PLW0603

    if _engine is not None:
        return _engine

    uri = global_config.BACKEND_DB_URI
    if not uri:
        raise RuntimeError(
            "BACKEND_DB_URI is not configured. "
            "Set it in your .env file to use database features."
        )

    _engine = create_engine(uri, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
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
