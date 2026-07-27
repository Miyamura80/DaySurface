"""Sliding-window rate limiting middleware using the ``limits`` library."""

import asyncio
import hashlib
import math
import os
import threading
import time
import uuid

from fastapi import Request, Response
from limits import (
    RateLimitItemPerDay,
    RateLimitItemPerHour,
    RateLimitItemPerMinute,
    RateLimitItemPerSecond,
)
from limits.storage import MemoryStorage, Storage
from limits.strategies import MovingWindowRateLimiter
from loguru import logger as log
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from api_server.auth.api_key_auth import get_user_id_for_key_hash
from api_server.auth.workos_auth import verify_workos_token

# Paths that bypass rate limiting.
from api_server.billing.stripe_config import STRIPE_WEBHOOK_PATH
from db.engine import use_db_session
from db.models.user_subscriptions import UserSubscription

_EXEMPT_PATHS = frozenset({"/health", "/mcp", "/ask", STRIPE_WEBHOOK_PATH})
_EXEMPT_PREFIXES = ("/mcp/",)

# TTL cache for API key hash → subscription tier (avoids DB hit on every request)
_tier_cache: dict[str, tuple[str, float]] = {}
_TIER_CACHE_TTL = 60  # seconds
_INVALID_KEY_TIER = "__invalid__"  # sentinel: API key not found in DB
_TIER_CACHE_MAX_SIZE = 10_000
_tier_cache_lock = threading.Lock()


def _build_storage() -> Storage:
    """Use Redis when REDIS_URL is set, otherwise in-memory."""
    redis_url = None
    try:
        # Deliberately lazy: config load happens per-call inside this
        # try so a failing/pruned config degrades to memory storage
        # (see the ImportError handler below) instead of crashing import.
        from common import global_config  # noqa: PLC0415

        redis_url = getattr(global_config, "REDIS_URL", None)
    except (ImportError, AttributeError):
        pass

    if redis_url:
        try:
            # Deliberately lazy: RedisStorage pulls in the optional redis
            # backend; an ImportError must degrade to MemoryStorage below.
            from limits.storage import RedisStorage  # noqa: PLC0415

            return RedisStorage(redis_url)
        except Exception as exc:  # noqa: BLE001
            # Redis client raises a wide range of errors (ImportError, ConnectionError,
            # AuthenticationError, TimeoutError); any of them must downgrade to
            # MemoryStorage so the API stays up.
            log.warning(
                "Redis unavailable for rate limiting ({}), falling back to memory storage",
                exc,
            )
            return MemoryStorage()

    log.debug(
        "REDIS_URL not set: rate limiting uses in-memory storage. "
        "Limits will not be enforced across multiple workers or replicas."
    )
    return MemoryStorage()


def _get_tier_limits(tier: str) -> dict:
    """Get rate limit values for a subscription tier from config."""
    try:
        # Deliberately lazy: config failures fall back to default limits
        # via the except (ImportError, ...) below (see _build_storage).
        from common import global_config  # noqa: PLC0415

        rate_limit_cfg = getattr(global_config, "rate_limit", None)
        if rate_limit_cfg and hasattr(rate_limit_cfg, "tiers"):
            tiers = rate_limit_cfg.tiers
            if isinstance(tiers, dict):
                tier_cfg = tiers.get(tier, tiers.get("default", {}))
                if isinstance(tier_cfg, dict):
                    return tier_cfg
    except (ImportError, AttributeError, KeyError) as exc:
        log.warning("Rate limit config lookup failed; applying defaults: {}", exc)
    # Defaults if config not available
    return {"rps": 5, "rpm": 60, "rph": 1000, "rpd": 5000}


def _trust_proxy_headers() -> bool:
    """Return True when the deployment sits behind a trusted reverse proxy."""
    try:
        # Deliberately lazy: config failures fall back to "don't trust
        # proxy headers" via the except (ImportError, ...) below.
        from common import global_config  # noqa: PLC0415

        rate_limit_cfg = getattr(global_config, "rate_limit", None)
        if rate_limit_cfg and hasattr(rate_limit_cfg, "trust_proxy_headers"):
            return bool(rate_limit_cfg.trust_proxy_headers)
    except (ImportError, AttributeError):
        pass
    return False


def _client_ip(request: Request) -> str:
    """Return the best-effort client IP from the request.

    Only trusts ``X-Real-IP`` when ``rate_limit.trust_proxy_headers`` is
    enabled in config, preventing header spoofing when the server is
    directly exposed without a reverse proxy.
    """
    if _trust_proxy_headers():
        ip = request.headers.get("X-Real-IP", "").strip()
        if ip:
            return ip
    if request.client:
        return request.client.host
    return "unknown_" + uuid.uuid4().hex


async def _identity(request: Request) -> str:
    """Resolve a stable identity for rate limiting.

    Priority: JWT user ID > API key hash > client IP.
    Matches the credential priority in unified_auth.get_authenticated_user
    so that rate-limit identity aligns with the authenticated principal.
    JWT users are keyed on their stable user ID (not the ephemeral token
    hash) so that token rotation does not reset rate-limit counters.

    Only the blocking ``verify_workos_token`` call is offloaded via
    ``asyncio.to_thread``; all other paths are pure in-memory work.

    Caches the resolved ``_rl_user_id`` on ``request.state`` so that
    ``_resolve_tier`` can reuse it without calling ``verify_workos_token``
    a second time.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            workos_user = await asyncio.to_thread(verify_workos_token, token)
            if workos_user:
                request.state._rl_user_id = workos_user.user_id
                return (
                    "user:" + hashlib.sha256(workos_user.user_id.encode()).hexdigest()
                )
        except Exception:  # noqa: BLE001
            # WorkOS verification can raise jose JWT errors, network errors, or
            # SDK-specific exceptions; on any failure fall through to the API-key
            # path rather than rejecting the request from middleware.
            pass
        # Mark that JWT resolution was attempted (even if it failed) so
        # _resolve_tier doesn't call verify_workos_token a second time.
        request.state._rl_user_id_resolved = True
        # Fall through to API key -- a valid key may still be present.

    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        # Validate the key before using it as a rate-limit bucket.
        # Without this, rotating fake keys creates unlimited fresh buckets.
        tier = await asyncio.to_thread(_lookup_tier_sync, key_hash)
        if tier == _INVALID_KEY_TIER:
            return "ip:" + _client_ip(request)
        # NOTE: Each API key gets its own rate-limit bucket.  A user with
        # N keys effectively has N x the tier limit.  This is intentional:
        # it allows distinct integrations (dev/prod/CI) to each consume
        # their own quota.  The billing-layer daily quota (ensure_daily_limit)
        # enforces a hard per-user cap regardless of key count.
        return "key:" + key_hash

    return "ip:" + _client_ip(request)


def _lookup_tier_sync(cache_key: str, *, user_id: str | None = None) -> str:
    """Look up subscription tier (synchronous, cached).

    Accepts either a cache_key derived from an API key hash or a user_id
    from a decoded JWT token. Uses a TTL cache to avoid a DB round-trip
    on every request.
    """
    cached = _tier_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]

    tier = "default"
    try:
        with use_db_session() as session:
            resolved_user_id = user_id
            if resolved_user_id is None:
                # Reuse the canonical key validity check from api_key_auth
                resolved_user_id = get_user_id_for_key_hash(session, cache_key)

            if resolved_user_id:
                sub = (
                    session.query(UserSubscription.subscription_tier)
                    .filter_by(user_id=resolved_user_id)
                    .first()
                )
                tier = sub.subscription_tier if sub else "default"
            elif user_id is None:
                # API key path: key hash not found in DB
                tier = _INVALID_KEY_TIER
    except Exception as exc:  # noqa: BLE001
        # DB / SQLAlchemy / connection errors all degrade to default tier so a
        # transient backend issue doesn't lock users out of the API.
        log.warning("Tier lookup failed for key {}; defaulting: {}", cache_key[:8], exc)
        # Don't cache failed lookups so the next request retries immediately
        return tier

    # Evict entries if cache is at capacity and then write the new entry,
    # all under the same lock to prevent the cache from transiently
    # exceeding _TIER_CACHE_MAX_SIZE under concurrent load.
    with _tier_cache_lock:
        if len(_tier_cache) >= _TIER_CACHE_MAX_SIZE:
            now = time.time()
            # First pass: remove expired entries
            expired = [k for k, (_, exp) in list(_tier_cache.items()) if exp <= now]
            for k in expired:
                _tier_cache.pop(k, None)
            # If still full, evict oldest 10% by expiry
            if len(_tier_cache) >= _TIER_CACHE_MAX_SIZE:
                snapshot = list(_tier_cache.items())
                by_expiry = sorted(snapshot, key=lambda x: x[1][1])
                evict_count = max(1, len(by_expiry) // 10)
                for k, _ in by_expiry[:evict_count]:
                    _tier_cache.pop(k, None)
        _tier_cache[cache_key] = (tier, time.time() + _TIER_CACHE_TTL)
    return tier


async def _resolve_tier(request: Request) -> str:
    """Resolve subscription tier from the request credentials.

    Performs a lightweight cached DB lookup so middleware doesn't depend on
    the auth dependency (which runs later, inside call_next).
    Supports both API key and JWT Bearer token authentication.
    """
    # Match credential priority from unified_auth: Bearer JWT first, then API key.
    # Reuse the user_id resolved by _identity() (cached on request.state)
    # to avoid calling verify_workos_token twice.
    cached_user_id = getattr(request.state, "_rl_user_id", None)
    if cached_user_id:
        cache_key = f"jwt:{cached_user_id}"
        return await asyncio.to_thread(
            _lookup_tier_sync, cache_key, user_id=cached_user_id
        )
    # If _identity already attempted JWT verification and it failed, don't
    # call verify_workos_token a second time; fall through to API key.
    if not getattr(request.state, "_rl_user_id_resolved", False):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                token = auth_header.removeprefix("Bearer ").strip()
                workos_user = await asyncio.to_thread(verify_workos_token, token)
                if workos_user:
                    cache_key = f"jwt:{workos_user.user_id}"
                    return await asyncio.to_thread(
                        _lookup_tier_sync, cache_key, user_id=workos_user.user_id
                    )
            except Exception:  # noqa: BLE001
                # Same rationale as _identity(): JWT verify can raise a wide
                # range of jose/network errors; on failure fall through to the
                # API-key path.
                pass

    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        tier = await asyncio.to_thread(_lookup_tier_sync, key_hash)
        return "default" if tier == _INVALID_KEY_TIER else tier

    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-request rate limiting with tier-aware sliding windows.

    Uses a two-phase ``test()`` then ``hit()`` approach instead of a
    single ``hit()`` per window.  This avoids over-decrementing earlier
    windows when a later window rejects, but introduces a TOCTOU gap
    where up to N concurrent workers can pass ``test()`` simultaneously
    before any of them calls ``hit()``.  Under sustained burst load this
    may briefly exceed the per-second limit.  The trade-off is acceptable
    for general API traffic; billing-critical endpoints are additionally
    protected by the atomic ``UPDATE...WHERE`` in ``ensure_daily_limit``.
    """

    _REDIS_RETRY_INTERVAL = 60  # seconds between Redis reconnect attempts

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._storage: Storage | None = None
        self._limiter: MovingWindowRateLimiter | None = None
        self._storage_is_memory = False
        self._last_storage_attempt = 0.0
        self._build_lock = threading.Lock()
        self._testing = os.getenv("TESTING") == "1"
        if self._testing:
            log.warning("Rate limiting disabled via TESTING=1 env var")

    def _get_limiter(self) -> MovingWindowRateLimiter:
        """Return the rate limiter, building storage lazily.

        If the previous attempt fell back to MemoryStorage (e.g. Redis
        was unreachable at startup), retry Redis periodically so that
        cross-worker enforcement recovers once Redis is healthy.
        """
        if self._limiter is not None and not self._storage_is_memory:
            return self._limiter
        # When using MemoryStorage, only retry Redis every _REDIS_RETRY_INTERVAL
        # seconds to avoid rebuilding storage (and losing counters) on every request.
        now = time.time()
        if (
            self._limiter is not None
            and self._storage_is_memory
            and now - self._last_storage_attempt < self._REDIS_RETRY_INTERVAL
        ):
            return self._limiter
        with self._build_lock:
            # Double-check after acquiring lock
            if self._limiter is not None and not self._storage_is_memory:
                return self._limiter
            now = time.time()
            if (
                self._limiter is not None
                and self._storage_is_memory
                and now - self._last_storage_attempt < self._REDIS_RETRY_INTERVAL
            ):
                return self._limiter
            self._last_storage_attempt = now

        # Build storage outside the lock so a slow Redis connection
        # attempt doesn't serialise all concurrent rate-limit checks.
        storage = _build_storage()
        is_memory = isinstance(storage, MemoryStorage)

        with self._build_lock:
            # Another thread may have built the limiter while we were
            # connecting to Redis.  Only install if upgrading to Redis,
            # or on first init.
            if self._limiter is not None and not self._storage_is_memory:
                return self._limiter
            if self._limiter is None or (not is_memory and self._storage_is_memory):
                self._storage = storage
                self._limiter = MovingWindowRateLimiter(storage)
                self._storage_is_memory = is_memory
            return self._limiter

    def _check_and_hit(
        self, windows: list, identity: str
    ) -> tuple[str | None, object | None, dict]:
        """Test all windows and consume a slot if allowed (synchronous).

        Returns ``(hit_window, hit_item, stats)`` where *stats* maps
        window names to ``(remaining, reset_time)`` collected during the
        test phase, avoiding a second round of ``get_window_stats`` calls.

        Offloaded to a thread via ``asyncio.to_thread`` so the blocking
        Redis I/O in ``test()`` / ``hit()`` does not stall the event loop.
        """
        limiter = self._get_limiter()
        stats: dict[str, tuple[int, float]] = {}
        for window_name, item in windows:
            ws = limiter.get_window_stats(item, identity)
            stats[window_name] = (max(0, ws.remaining), ws.reset_time)
            if ws.remaining <= 0:
                return window_name, item, stats
        for _, item in windows:
            limiter.hit(item, identity)
        return None, None, stats

    def _should_skip(self, request: Request) -> bool:
        """Return True if this request should bypass rate limiting."""
        if request.url.path in _EXEMPT_PATHS:
            return True
        if any(request.url.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return True
        if self._testing:
            return True
        try:
            # Deliberately lazy: config failures must not disable the
            # exemption check; the except (ImportError, ...) below applies.
            from common import global_config  # noqa: PLC0415

            rate_limit_cfg = getattr(global_config, "rate_limit", None)
            if (
                rate_limit_cfg
                and hasattr(rate_limit_cfg, "enabled")
                and not rate_limit_cfg.enabled
            ):
                return True
        except (ImportError, AttributeError):
            pass
        return False

    # Map window name → config key so 429 headers reflect the exceeded window.
    _WINDOW_CFG_KEY = {"second": "rps", "minute": "rpm", "hour": "rph", "day": "rpd"}

    def _build_429(
        self,
        request: Request,
        hit_window: str,
        hit_item,
        identity: str,
        limits_cfg: dict,
        precomputed_reset: float | None = None,
    ) -> JSONResponse:
        """Build a 429 Too Many Requests response."""
        cfg_key = self._WINDOW_CFG_KEY.get(hit_window, "rpm")
        exceeded_limit = limits_cfg.get(cfg_key, 60)
        if precomputed_reset is not None:
            reset_ts = int(precomputed_reset)
            retry_after = max(1, math.ceil(precomputed_reset - time.time()))
        else:
            try:
                hit_stats = self._get_limiter().get_window_stats(hit_item, identity)
                retry_after = max(1, math.ceil(hit_stats.reset_time - time.time()))
                reset_ts = int(hit_stats.reset_time)
            except Exception:  # noqa: BLE001
                # Storage backend may raise Redis/connection errors when computing
                # stats; fall back to a 60s Retry-After so the 429 still ships.
                retry_after = 60
                reset_ts = int(time.time()) + 60
        headers = {
            "X-RateLimit-Limit": str(exceeded_limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_ts),
            "RateLimit": f"limit={exceeded_limit}, remaining=0, reset={reset_ts}",
            "RateLimit-Policy": f"{limits_cfg.get('rps', 5)};w=1, {limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600, {limits_cfg.get('rpd', 5000)};w=86400",
            "Retry-After": str(retry_after),
        }
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": f"Rate limit exceeded ({hit_window} window). Retry after {retry_after}s.",
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            headers=headers,
        )

    @staticmethod
    def _stats_from_collected(
        window_stats: dict[str, tuple[int, float]], limits_cfg: dict
    ) -> tuple[int, int, float]:
        """Pick the most constrained window from pre-collected stats."""
        best_remaining: int | None = None
        reset_time = time.time() + 60
        limit_val = limits_cfg.get("rpm", 60)
        for window_name, (rem, rst) in window_stats.items():
            if best_remaining is None or rem < best_remaining:
                best_remaining = rem
                cfg_key = RateLimitMiddleware._WINDOW_CFG_KEY.get(window_name, "rpm")
                limit_val = limits_cfg.get(cfg_key, 60)
                reset_time = rst
        return (
            limit_val,
            best_remaining if best_remaining is not None else 0,
            reset_time,
        )

    async def _resolve_request_context(
        self, request: Request
    ) -> tuple[str, str] | None:
        """Resolve identity and tier for a request.

        Returns ``(identity, tier)`` or ``None`` on failure.
        """
        try:
            identity = await _identity(request)
            tier = await _resolve_tier(request)
            return identity, tier
        except Exception:  # noqa: BLE001
            # Identity/tier resolution touches DB, Redis, JWT verification, and
            # config loading. Any failure must fail-open (allow the request) so
            # a single subsystem hiccup doesn't black-hole all traffic.
            log.warning(
                "Rate limiter identity/tier resolution error; allowing request through"
            )
            return None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._should_skip(request):
            return await call_next(request)

        ctx = await self._resolve_request_context(request)
        if ctx is None:
            return await call_next(request)
        identity, tier = ctx
        limits_cfg = _get_tier_limits(tier)

        # Build rate limit items for each window
        windows = [
            ("second", RateLimitItemPerSecond(limits_cfg.get("rps", 5))),
            ("minute", RateLimitItemPerMinute(limits_cfg.get("rpm", 60))),
            ("hour", RateLimitItemPerHour(limits_cfg.get("rph", 1000))),
            ("day", RateLimitItemPerDay(limits_cfg.get("rpd", 5000))),
        ]

        # Check all windows before consuming quota to avoid over-decrementing
        # earlier windows when a later window rejects.  Known trade-off: the
        # TOCTOU gap between test() and hit() means up to N concurrent
        # workers can pass test() simultaneously, briefly exceeding the RPS
        # limit.  This is acceptable because (a) over-decrementing under
        # sustained load is worse, and (b) the limits library's hit()
        # doesn't expose window stats needed for response headers.
        # Wrap in try/except so a Redis failure degrades gracefully (pass
        # through) rather than converting every request to a 500.
        try:
            hit_window, hit_item, window_stats = await asyncio.to_thread(
                self._check_and_hit, windows, identity
            )
        except Exception:  # noqa: BLE001
            # Fail-open on storage backend errors (Redis outage, network); the
            # alternative is converting every request to a 500 during incidents.
            log.warning("Rate limiter error; allowing request through")
            return await call_next(request)

        if hit_window is not None and hit_item is not None:
            _, rst = window_stats.get(hit_window, (0, time.time() + 60))
            return self._build_429(
                request,
                hit_window,
                hit_item,
                identity,
                limits_cfg,
                precomputed_reset=rst,
            )

        # Use stats collected during _check_and_hit to find the most
        # constrained window (lowest remaining) without extra Redis calls.
        limit_val, remaining, reset_time = self._stats_from_collected(
            window_stats, limits_cfg
        )

        response = await call_next(request)

        # Attach rate limit headers (remaining is pre-hit: subtract 1)
        post_hit_remaining = max(0, remaining - 1)
        response.headers["X-RateLimit-Limit"] = str(limit_val)
        response.headers["X-RateLimit-Remaining"] = str(post_hit_remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_time))
        response.headers["RateLimit"] = (
            f"limit={limit_val}, remaining={post_hit_remaining}, reset={int(reset_time)}"
        )
        response.headers["RateLimit-Policy"] = (
            f"{limits_cfg.get('rps', 5)};w=1, {limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600, {limits_cfg.get('rpd', 5000)};w=86400"
        )

        return response
