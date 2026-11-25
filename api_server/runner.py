"""In-process periodic runners started from the FastAPI lifespan.

Webhook loop: started when ``WEBHOOK_RUNNER_MODE="loop"`` and a Pub/Sub topic
is configured. Each tick drains due webhook deliveries; every
``_MAINTENANCE_EVERY_TICKS`` ticks it also renews near-expiry Gmail watches and
prunes old delivered rows. When ``WEBHOOK_RUNNER_MODE="endpoint"`` this loop
stays dormant and the same work is driven externally via
``POST /api/v1/google/internal/renew``.

OAuth purge loop: started when ``MCP_AUTH_MODE=self``; hourly it deletes the
built-in OAuth server's expired codes and tokens
(``api_server.oauth.storage.purge_expired``).

All work runs in worker threads so the event loop stays responsive, and a tick
failure can never kill a loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger as log

from common import global_config
from services.gmail_watch_svc import renew_due_watches
from services.webhook_delivery_svc import cleanup_delivered, drain_due_deliveries

# At the default 30s tick this runs maintenance roughly hourly.
_MAINTENANCE_EVERY_TICKS = 120
_OAUTH_PURGE_INTERVAL_S = 3600


async def _tick(counter: int) -> None:
    await asyncio.to_thread(drain_due_deliveries)
    if counter % _MAINTENANCE_EVERY_TICKS == 0:
        await asyncio.to_thread(renew_due_watches)
        await asyncio.to_thread(cleanup_delivered)


async def _run_loop() -> None:
    interval = max(5, global_config.WEBHOOK_RUNNER_INTERVAL_S)
    log.info("Webhook runner loop started (interval={}s)", interval)
    counter = 0
    while True:
        counter += 1
        try:
            await _tick(counter)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Loop resilience: a single tick's failure (DB blip, Gmail error)
            # must never terminate the runner - log and continue next tick.
            log.warning("Webhook runner tick failed: {}", exc)
        await asyncio.sleep(interval)


def runner_enabled() -> bool:
    return global_config.WEBHOOK_RUNNER_MODE == "loop" and bool(
        global_config.GMAIL_PUBSUB_TOPIC
    )


def oauth_purge_enabled() -> bool:
    return global_config.MCP_AUTH_MODE == "self"


async def _oauth_purge_loop() -> None:
    # Imported here so the default authkit mode never loads the OAuth server.
    from api_server.oauth.storage import purge_expired  # noqa: PLC0415

    while True:
        try:
            removed = await asyncio.to_thread(purge_expired)
            if removed:
                log.info("Purged {} expired OAuth codes/tokens", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Loop resilience: a DB blip must not stop future purges; the rows
            # it would have removed are simply removed next hour.
            log.warning("OAuth purge failed: {}", exc)
        await asyncio.sleep(_OAUTH_PURGE_INTERVAL_S)


@asynccontextmanager
async def runner_lifespan(_app) -> AsyncIterator[None]:
    """Start the enabled runner loops on enter, cancel them cleanly on shutdown."""
    tasks = []
    if runner_enabled():
        tasks.append(asyncio.create_task(_run_loop()))
    if oauth_purge_enabled():
        tasks.append(asyncio.create_task(_oauth_purge_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
