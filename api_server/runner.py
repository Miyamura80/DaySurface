"""In-process periodic runner: outbox draining + watch renewal + cleanup.

Started from the FastAPI lifespan when ``WEBHOOK_RUNNER_MODE="loop"`` and a
Pub/Sub topic is configured. Each tick drains due webhook deliveries; every
``_MAINTENANCE_EVERY_TICKS`` ticks it also renews near-expiry Gmail watches and
prunes old delivered rows. All work runs in worker threads so the event loop
stays responsive, and a tick failure can never kill the loop.

When ``WEBHOOK_RUNNER_MODE="endpoint"`` this loop stays dormant and the same
work is driven externally via ``POST /api/v1/google/internal/renew``.
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


@asynccontextmanager
async def runner_lifespan(_app) -> AsyncIterator[None]:
    """Start the runner loop on enter, cancel it cleanly on shutdown."""
    task = asyncio.create_task(_run_loop()) if runner_enabled() else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
