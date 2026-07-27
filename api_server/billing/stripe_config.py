"""Lazy Stripe initialization with graceful degradation."""

import threading

from loguru import logger as log

from common import global_config

# Canonical webhook path shared by the route module and rate-limit middleware.
STRIPE_WEBHOOK_PATH = "/api/v1/billing/webhook/stripe"

# Environments where test keys / webhook fallback are allowed.
# Any value NOT in this set is treated as production (fail-closed).
_NON_PROD_ENVS = frozenset({"dev", "development", "staging", "test", "local"})


def is_production() -> bool:
    """Return True if the current environment is considered production."""
    return global_config.DEV_ENV.lower() not in _NON_PROD_ENVS


_stripe_initialized = False
_stripe_lock = threading.Lock()


def ensure_stripe() -> bool:
    """Initialize Stripe SDK. Returns False if no keys are configured.

    Negative results are never cached so that secrets injected after
    startup (e.g. Railway / Kubernetes delayed secret binding) are
    picked up on the next call without a restart.

    The positive cache is permanent unless explicitly reset via
    ``reset_stripe_on_auth_error()``, which should be called when a
    Stripe ``AuthenticationError`` is caught.  This allows key rotation
    via Railway/Kubernetes secret injection without a full restart.
    """
    global _stripe_initialized  # noqa: PLW0603
    if _stripe_initialized:
        return True

    with _stripe_lock:
        if _stripe_initialized:
            return True

        try:
            # Deliberately lazy: the app must boot (and billing degrade to
            # 503 via the False return) when the stripe SDK is pruned from
            # the template or fails to import.
            import stripe  # noqa: PLC0415

            cfg = global_config.subscription_config.stripe

            # Pick the right key based on environment
            if is_production():
                key = global_config.STRIPE_SECRET_KEY
            else:
                key = global_config.STRIPE_TEST_SECRET_KEY
                if not key:
                    if getattr(global_config, "STRIPE_ALLOW_LIVE_KEY_IN_DEV", False):
                        log.warning(
                            "STRIPE_TEST_SECRET_KEY not set in non-prod (DEV_ENV={}); "
                            "using live key because STRIPE_ALLOW_LIVE_KEY_IN_DEV is set",
                            global_config.DEV_ENV,
                        )
                        key = global_config.STRIPE_SECRET_KEY
                    elif global_config.STRIPE_SECRET_KEY:
                        log.error(
                            "STRIPE_TEST_SECRET_KEY not set in non-prod (DEV_ENV={}); "
                            "refusing to fall back to live STRIPE_SECRET_KEY to prevent "
                            "real charges. Set STRIPE_TEST_SECRET_KEY or "
                            "STRIPE_ALLOW_LIVE_KEY_IN_DEV=true to override",
                            global_config.DEV_ENV,
                        )
                        return False

            if not key:
                log.debug("Stripe not configured - billing features disabled")
                return False

            stripe.api_key = key
            stripe.api_version = cfg.api_version
            _stripe_initialized = True
            log.info("Stripe SDK initialized (api_version={})", cfg.api_version)
            return True
        except Exception as exc:  # noqa: BLE001
            # Stripe SDK init can fail on env, network, or auth; report and let
            # the next request retry. Billing routes detect the False return.
            log.warning("Failed to initialize Stripe; will retry on next call: {}", exc)
            return False


def reset_stripe_on_auth_error() -> None:
    """Reset initialization flag so the next call to ``ensure_stripe()`` re-reads keys.

    Call this when a ``stripe.AuthenticationError`` is caught to allow
    rotated secrets to take effect without a process restart.
    """
    global _stripe_initialized  # noqa: PLW0603
    with _stripe_lock:
        _stripe_initialized = False
    log.warning(
        "Stripe initialization reset due to authentication error; will re-init on next call"
    )


def get_stripe_price_id() -> str:
    """Return the Stripe price ID for the current environment."""
    cfg = global_config.subscription_config.stripe
    if is_production():
        return cfg.price_ids.get("prod", "")
    return cfg.price_ids.get("test", "")


def get_meter_event_name() -> str:
    """Return the Stripe Billing Meter event name."""
    return global_config.subscription_config.stripe.meter_event_name


def get_included_units() -> int:
    """Return the number of included metered units per period."""
    return global_config.subscription_config.metered.included_units


def get_webhook_secret() -> str | None:
    """Return the Stripe webhook signing secret for the current environment."""
    if is_production():
        return global_config.STRIPE_WEBHOOK_SECRET
    return (
        global_config.STRIPE_TEST_WEBHOOK_SECRET or global_config.STRIPE_WEBHOOK_SECRET
    )
