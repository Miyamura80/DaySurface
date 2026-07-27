"""Health-check endpoint with component status (no auth required)."""

import collections.abc
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter
from loguru import logger as log

try:
    _APP_VERSION = _pkg_version("mcp-template")
except PackageNotFoundError:
    _APP_VERSION = "0.1.0"

router = APIRouter(tags=["health"])

# TTL cache for component health checks (avoids DB/Redis hit on every poll)
_HEALTH_TTL = 15  # seconds
_health_cache: dict[str, tuple[dict, float]] = {}
_health_locks: dict[str, threading.Lock] = {}
_health_locks_meta = threading.Lock()


def _get_component_lock(name: str) -> threading.Lock:
    """Return a per-component lock, creating one lazily if needed."""
    lock = _health_locks.get(name)
    if lock is not None:
        return lock
    with _health_locks_meta:
        if name not in _health_locks:
            _health_locks[name] = threading.Lock()
        return _health_locks[name]


def _cached_check(name: str, check_fn: collections.abc.Callable[[], dict]) -> dict:
    """Return cached result if fresh, otherwise call check_fn under lock.

    Each component has its own lock so a slow DB probe doesn't block
    Redis or Stripe checks.  With a 15 s TTL the serialisation is
    negligible for health endpoints.
    """
    cached = _health_cache.get(name)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    with _get_component_lock(name):
        # Double-check after acquiring lock
        cached = _health_cache.get(name)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]
        result = check_fn()
        _health_cache[name] = (result, time.monotonic() + _HEALTH_TTL)
    return result


def _check_database() -> dict:
    """Check database connectivity using the app's singleton engine."""
    try:
        # Deliberately lazy: probe imports live inside the try so a pruned
        # or broken config/DB stack reports a status instead of breaking
        # the /health route at import time.
        from common import global_config  # noqa: PLC0415

        if not global_config.BACKEND_DB_URI:
            return {"status": "not_configured"}
        # (same probe rationale as above)
        from sqlalchemy import text  # noqa: PLC0415

        from db.engine import use_db_session  # noqa: PLC0415

        with use_db_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        # Health probe: must report any DB/connection/config failure mode as a
        # structured status, never propagate.
        return {"status": "error", "message": type(exc).__name__}


_redis_health_client: object | None = None
_redis_health_client_lock = threading.Lock()
_REDIS_NOT_CONFIGURED = object()  # sentinel: REDIS_URL was absent


def _get_redis_health_client():
    """Return a reusable Redis client for health checks.

    Returns ``None`` when no ``REDIS_URL`` is configured (the
    ``_REDIS_NOT_CONFIGURED`` sentinel is used internally to
    distinguish "not configured" from "not yet created").
    Creates and caches a new client on first call, then returns
    the cached instance on subsequent calls.
    """
    global _redis_health_client  # noqa: PLW0603
    val = _redis_health_client
    if val is _REDIS_NOT_CONFIGURED:
        return None
    if val is not None:
        return val
    with _redis_health_client_lock:
        val = _redis_health_client
        if val is _REDIS_NOT_CONFIGURED:
            return None
        if val is not None:
            return val
        # Deliberately lazy: callers (_check_redis) treat ImportError like
        # any other probe failure, so a pruned config or redis package
        # degrades to an error status instead of breaking module import.
        from common import global_config  # noqa: PLC0415

        redis_url = getattr(global_config, "REDIS_URL", None)
        if not redis_url:
            _redis_health_client = _REDIS_NOT_CONFIGURED
            return None
        # (same probe rationale as above)
        import redis  # noqa: PLC0415

        _redis_health_client = redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        return _redis_health_client


def _check_redis() -> dict:
    """Check Redis connectivity."""
    global _redis_health_client  # noqa: PLW0603
    try:
        client = _get_redis_health_client()
        if client is None:
            return {"status": "not_configured"}
        client.ping()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        # Health probe: any Redis client error (connection, auth, timeout) maps
        # to a status string; reset the cached client so the next probe retries.
        with _redis_health_client_lock:
            _redis_health_client = None
        return {"status": "error", "message": type(exc).__name__}


def _check_stripe() -> dict:
    """Check Stripe SDK initialization (config + key presence).

    Does not make a live API call to avoid hitting Stripe rate limits
    from frequent health probes.
    """
    try:
        # Deliberately lazy: the except below explicitly maps "Stripe SDK /
        # config / import failure" to a status string; imports must stay
        # inside the try for that to hold when billing is pruned.
        from common import global_config  # noqa: PLC0415

        has_key = bool(
            getattr(global_config, "STRIPE_SECRET_KEY", None)
            or getattr(global_config, "STRIPE_TEST_SECRET_KEY", None)
        )
        if not has_key:
            return {"status": "not_configured"}

        # (same probe rationale as above)
        from api_server.billing.stripe_config import ensure_stripe  # noqa: PLC0415

        if not ensure_stripe():
            return {"status": "error", "message": "initialization_failed"}

        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        # Health probe: report any Stripe SDK / config / import failure as a
        # structured status rather than crashing the /health endpoint.
        return {"status": "error", "message": type(exc).__name__}


_git_commit_lock = threading.Lock()
_git_commit_value: str | None = None
_git_commit_resolved = False


def _get_git_commit() -> str | None:
    """Get current git commit hash (cached after first call).

    Prefers build-time env vars (GIT_SHA, RENDER_GIT_COMMIT) for
    containerized deployments where git may not be available.
    Production deployments should always set GIT_SHA so the subprocess
    fallback is never invoked (it adds latency on cold start and requires
    git to be installed in the container image).
    Uses double-checked locking so the subprocess runs exactly once,
    even under concurrent cold-start calls.  Executes in FastAPI's
    sync-endpoint threadpool, so it does not block the async event loop.
    """
    global _git_commit_value, _git_commit_resolved
    if _git_commit_resolved:
        return _git_commit_value
    with _git_commit_lock:
        if _git_commit_resolved:
            return _git_commit_value
        for var in ("GIT_SHA", "RENDER_GIT_COMMIT"):
            # Prefer build-time env vars; the subprocess fallback below
            # requires git in the container image and adds cold-start latency.
            val = os.getenv(var)
            if val:
                _git_commit_value = val[:7]
                _git_commit_resolved = True
                return _git_commit_value
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            _git_commit_value = (
                result.stdout.strip() if result.returncode == 0 else None
            )
        except (OSError, subprocess.SubprocessError):
            _git_commit_value = None
        if _git_commit_value is None:
            log.warning(
                "Could not determine git commit (GIT_SHA not set and git unavailable); "
                "set GIT_SHA at build time for production deployments"
            )
        _git_commit_resolved = True
        return _git_commit_value


@router.get("/health")
def health_check():
    components = {
        "api": {"status": "ok"},
        "database": _cached_check("database", _check_database),
        "redis": _cached_check("redis", _check_redis),
        "stripe": _cached_check("stripe", _check_stripe),
    }

    # "ok" if all components are ok or not_configured; "degraded" if any errored
    overall = "ok"
    for comp in components.values():
        if comp["status"] == "error":
            overall = "degraded"
            break

    return {
        "status": overall,
        "version": _APP_VERSION,
        "commit": _get_git_commit(),
        "timestamp": datetime.now(UTC).isoformat(),
        "components": components,
    }
