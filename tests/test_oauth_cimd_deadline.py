"""The CIMD fetch deadline holds against servers that stall mid-response.

Each attack keeps the connection busy with bytes that arrive just inside the
per-read timeout, so only a deadline enforced on every socket operation cuts
it off. A real loopback socket server is used because the stalls happen inside
httpcore/h11, below anything a mock of httpx would exercise.
"""

import socket
import threading
import time
from unittest.mock import patch

import pytest

from api_server.oauth import cimd
from services._ssrf_guard import PinnedTarget
from tests.oauth_server_fixture import client_document
from tests.test_template import TestTemplate

_DEADLINE = 0.5
_LIMIT = 1.5  # what a fetch may take, deadline plus generous slack
_ATTACK_SECONDS = 5.0  # how long the server keeps an attack going
_TICK = 0.05  # well inside the per-read timeout, which equals the deadline


def _drip(conn: socket.socket, data: bytes) -> None:
    for byte in data:
        conn.sendall(bytes([byte]))
        time.sleep(_TICK)


def _continue_flood(conn: socket.socket) -> None:
    end = time.monotonic() + _ATTACK_SECONDS
    while time.monotonic() < end:
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        time.sleep(_TICK)


def _header_trickle(conn: socket.socket) -> None:
    pad = b"a" * int(_ATTACK_SECONDS / _TICK)
    _drip(conn, b"HTTP/1.1 200 OK\r\nX-Pad: " + pad)


def _trailer_trickle(conn: socket.socket) -> None:
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\n{\r\n0\r\n"
    )
    _drip(conn, b"X-T: " + b"a" * int(_ATTACK_SECONDS / _TICK))


class _Server:
    """A loopback HTTP server whose behaviour is picked by the request path."""

    def __init__(self, documents: dict[str, bytes]) -> None:
        self.documents = documents
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:  # closed by the test
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    data = conn.recv(65536)
                    if not data:
                        return
                    request += data
                path = request.split(b" ", 2)[1].decode()
                self._respond(conn, path)
            except OSError:  # the client hung up, which is the point
                return

    def _respond(self, conn: socket.socket, path: str) -> None:
        if path.startswith("/flood"):
            _continue_flood(conn)
        elif path.startswith("/header"):
            _header_trickle(conn)
        elif path.startswith("/trailer"):
            _trailer_trickle(conn)
        else:
            body = self.documents[path]
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )

    def close(self) -> None:
        self.sock.close()


class TestCimdFetchDeadline(TestTemplate):
    @pytest.fixture(autouse=True)
    def _server(self):
        cimd.clear_cache()
        self.stall_server = _Server({})
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

    @pytest.mark.parametrize("mode", ["flood", "header", "trailer"])
    def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        with pytest.raises(cimd.ClientMetadataError):
            cimd._fetch_document(f"https://client.test.example/{mode}.json")
        assert time.monotonic() - start < _LIMIT

    def test_stalled_fetches_free_their_slots_at_the_deadline(self):
        errors: list[Exception] = []

        def hold(n: int) -> None:
            try:
                cimd.resolve_client(f"https://client.test.example/header-{n}.json")
            except cimd.ClientMetadataError as exc:
                errors.append(exc)

        holders = [threading.Thread(target=hold, args=(n,)) for n in range(4)]
        for thread in holders:
            thread.start()
        time.sleep(0.2)  # let all four take a slot
        start = time.monotonic()
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.resolve_client("https://client.test.example/ok.json")
        assert time.monotonic() - start < 0.1  # refused, not queued

        for thread in holders:
            thread.join(timeout=_LIMIT)
        assert not any(thread.is_alive() for thread in holders)
        assert len(errors) == 4

        client_id = "https://client.test.example/ok.json"
        self.stall_server.documents["/ok.json"] = client_document(client_id=client_id)
        assert cimd.resolve_client(client_id).client_id == client_id
