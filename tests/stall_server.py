"""A loopback HTTP server that stalls mid-response, for fetch-deadline tests.

Each attack keeps the connection busy with bytes that arrive just inside the
per-read timeout, so only a deadline enforced on every socket operation cuts
it off. A real socket server is used because the stalls happen inside
httpcore/h11, below anything a mock of httpx would exercise.

The request path picks the behaviour: ``/flood...`` sends endless ``100
Continue`` responses, ``/header...`` trickles a header, ``/trailer...`` a chunk
trailer, ``/body...`` a body; any other path serves ``documents[path]``.
"""

import socket
import threading
import time

ATTACK_SECONDS = 5.0  # how long the server keeps an attack going
TICK = 0.05  # well inside the per-read timeout of every test using this


def _drip(conn: socket.socket, data: bytes) -> None:
    for byte in data:
        conn.sendall(bytes([byte]))
        time.sleep(TICK)


def _continue_flood(conn: socket.socket) -> None:
    end = time.monotonic() + ATTACK_SECONDS
    while time.monotonic() < end:
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        time.sleep(TICK)


def _header_trickle(conn: socket.socket) -> None:
    pad = b"a" * int(ATTACK_SECONDS / TICK)
    _drip(conn, b"HTTP/1.1 200 OK\r\nX-Pad: " + pad)


def _trailer_trickle(conn: socket.socket) -> None:
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\n{\r\n0\r\n"
    )
    _drip(conn, b"X-T: " + b"a" * int(ATTACK_SECONDS / TICK))


def _body_trickle(conn: socket.socket) -> None:
    size = int(ATTACK_SECONDS / TICK)
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n"
        + f"Content-Length: {size}\r\n\r\n".encode()
    )
    _drip(conn, b"a" * size)


class StallServer:
    """A loopback HTTP server whose behaviour is picked by the request path."""

    def __init__(self, documents: dict[str, bytes] | None = None) -> None:
        self.documents = documents if documents is not None else {}
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
        elif path.startswith("/body"):
            _body_trickle(conn)
        else:
            body = self.documents[path]
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )

    def close(self) -> None:
        self.sock.close()
