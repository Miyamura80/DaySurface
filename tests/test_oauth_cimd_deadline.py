"""The CIMD fetch deadline holds against servers that stall mid-response.

See ``tests/stall_server.py`` for the attacks.
"""

import threading
import time
from unittest.mock import patch

import pytest

from api_server.oauth import cimd
from services._ssrf_guard import PinnedTarget
from tests.oauth_server_fixture import client_document
from tests.stall_server import StallServer
from tests.test_template import TestTemplate

_DEADLINE = 0.5
_LIMIT = 1.5  # what a fetch may take, deadline plus generous slack


class TestCimdFetchDeadline(TestTemplate):
    @pytest.fixture(autouse=True)
    def _server(self):
        cimd.clear_cache()
        self.stall_server = StallServer()
        with (
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", _DEADLINE),
            patch.object(cimd, "validate_and_pin", side_effect=self._pin),
        ):
            yield
        self.stall_server.close()
        cimd.clear_cache()

    def _pin(self, client_id: str) -> PinnedTarget:
        # https://client.test.example/<path> -> the loopback server's <path>
        path = "/" + client_id.split("/", 3)[3]
        return PinnedTarget(url=self.stall_server.url(path))

    @pytest.mark.parametrize("mode", ["flood", "header", "trailer", "body"])
    def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        target = self._pin(f"https://client.test.example/{mode}.json")
        with pytest.raises(cimd.ClientMetadataError):
            cimd._fetch_document(target, deadline=start + _DEADLINE)
        assert time.monotonic() - start < _LIMIT

    def test_stalled_fetches_free_their_slots_at_the_deadline(self):
        per_target = cimd._MAX_FETCHES_PER_TARGET
        entered = threading.Barrier(per_target + 1)
        real_fetch = cimd._fetch_document
        errors: list[Exception] = []

        def fetch(target: PinnedTarget, deadline: float) -> bytes:
            if "/header-" in target.url:
                entered.wait(_LIMIT)  # this fetch holds its slot
            return real_fetch(target, deadline)

        def hold(n: int) -> None:
            try:
                cimd.resolve_client(f"https://client.test.example/header-{n}.json")
            except cimd.ClientMetadataError as exc:
                errors.append(exc)

        holders = [threading.Thread(target=hold, args=(n,)) for n in range(per_target)]
        with patch.object(cimd, "_fetch_document", side_effect=fetch):
            for thread in holders:
                thread.start()
            entered.wait(_LIMIT)
            # Every stall server address is loopback, so one target.
            start = time.monotonic()
            with pytest.raises(cimd.ClientMetadataError, match="too many"):
                cimd.resolve_client("https://client.test.example/ok.json")
            assert time.monotonic() - start < _DEADLINE  # refused at once

            for thread in holders:
                thread.join(timeout=_LIMIT)
        assert not any(thread.is_alive() for thread in holders)
        assert len(errors) == per_target
        state = cimd.snapshot()
        assert state.in_flight == ()
        assert state.fetches.running == 0

        client_id = "https://client.test.example/ok.json"
        self.stall_server.documents["/ok.json"] = client_document(client_id=client_id)
        assert cimd.resolve_client(client_id).client_id == client_id
