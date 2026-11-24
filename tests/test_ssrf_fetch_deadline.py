"""Outbound SSRF-guarded fetches hold their deadline against stalling servers.

Image URLs come from attacker-controlled email (and the inbox app fires many
fetches at once); webhook subscribers are third-party servers. Either may
trickle bytes just inside the per-read timeout, so only a deadline enforced on
every socket operation cuts it off. Uses the real loopback stall server in
``tests/stall_server.py``: the stalls happen inside httpcore/h11, below anything
a mock of httpx would exercise.
"""

import time
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from db.models.webhooks import WebhookDelivery, WebhookEvent
from services import image_proxy, webhook_delivery_svc
from services._ssrf_guard import PinnedTarget
from tests.stall_server import StallServer
from tests.test_template import TestTemplate

_DEADLINE = 0.5
_LIMIT = 1.5  # what a fetch may take, deadline plus generous slack


class TestImageFetchDeadline(TestTemplate):
    @pytest.fixture(autouse=True)
    def _server(self):
        self.stall_server = StallServer()
        with (
            patch.object(image_proxy, "_TOTAL_DEADLINE_SECONDS", _DEADLINE),
            patch.object(image_proxy, "validate_and_pin", side_effect=self._pin),
        ):
            yield
        self.stall_server.close()

    def _pin(self, url: str) -> PinnedTarget:
        # https://img.test.example/<path> -> the loopback server's <path>
        path = "/" + url.split("/", 3)[3]
        return PinnedTarget(url=self.stall_server.url(path))

    @pytest.mark.parametrize("mode", ["flood", "header", "trailer"])
    def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        with pytest.raises(image_proxy.ImageFetchError):
            image_proxy.fetch_remote_image(f"https://img.test.example/{mode}.png")
        assert time.monotonic() - start < _LIMIT


class TestWebhookDeliveryDeadline(TestTemplate):
    @pytest.fixture(autouse=True)
    def _server(self):
        self.stall_server = StallServer()
        with (
            patch.object(webhook_delivery_svc, "_DELIVERY_DEADLINE_S", _DEADLINE),
            patch.object(
                webhook_delivery_svc, "validate_and_pin", side_effect=self._pin
            ),
        ):
            yield
        self.stall_server.close()

    def _pin(self, url: str, **_kwargs) -> PinnedTarget:
        path = "/" + url.split("/", 3)[3]
        return PinnedTarget(url=self.stall_server.url(path))

    def test_stalling_subscriber_is_cut_off_at_the_deadline(self):
        event = WebhookEvent(
            id="evt_1",
            event_type="message.received",
            created_at=datetime.now(UTC),
            payload={},
        )
        delivery = WebhookDelivery(id="dlv_1")
        start = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            webhook_delivery_svc._post(
                "https://hooks.test.example/header", "whsec_x", delivery, event
            )
        assert time.monotonic() - start < _LIMIT
