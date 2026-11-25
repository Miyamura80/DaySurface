"""The cached Gmail client is safe to share across threads.

``_get_gmail_client`` caches one client per user and every worker thread
serving that user reuses it (the MCP composer fires several tool calls at
once). httplib2 connections are not thread-safe, so the client must give each
request its own connection; sharing one interleaved TLS records in production
(``ssl.SSLError: record layer failure``) and crashed the server. These tests
pin Google's per-request ``Http`` pattern over a real local socket.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build_from_document
from googleapiclient.discovery_cache import get_static_doc

from services._gmail_retry import RateLimitRetryingRequest
from services.gmail_svc import _gmail_build_kwargs
from tests.test_template import TestTemplate

_LABELS = json.dumps({"labels": [{"id": "INBOX", "name": "INBOX"}]}).encode()


class _Recorder(BaseHTTPRequestHandler):
    # One HTTP/1.1 connection can carry many requests, so the client's source
    # port identifies the connection a request rode on.
    protocol_version = "HTTP/1.1"
    seen: list[tuple[int, str | None]] = []
    lock = threading.Lock()

    def do_GET(self) -> None:
        with self.lock:
            self.seen.append(
                (self.client_address[1], self.headers.get("Authorization"))
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_LABELS)))
        self.end_headers()
        self.wfile.write(_LABELS)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


class TestGmailClientThreading(TestTemplate):
    @pytest.fixture(autouse=True)
    def fake_gmail(self):
        _Recorder.seen = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        doc = json.loads(get_static_doc("gmail", "v1"))
        doc["rootUrl"] = f"http://127.0.0.1:{server.server_port}/"
        creds = Credentials(token="at-shared")  # noqa: S106 - test token
        # The same build arguments _get_gmail_client uses for the cached client.
        self.client = build_from_document(doc, **_gmail_build_kwargs(creds))
        yield
        server.shutdown()
        server.server_close()

    def _list(self):
        return self.client.users().labels().list(userId="me")

    def test_each_request_gets_its_own_connection(self):
        first, second = self._list(), self._list()
        assert first.http is not second.http
        assert isinstance(first, RateLimitRetryingRequest)

    def test_concurrent_calls_on_one_cached_client_all_succeed(self):
        calls = 40
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self._list().execute(), range(calls)))

        assert all(r["labels"][0]["id"] == "INBOX" for r in results)
        assert len(_Recorder.seen) == calls
        # Every request is still authorized with the shared credentials...
        assert {auth for _, auth in _Recorder.seen} == {"Bearer at-shared"}
        # ...and none shared a connection with another.
        assert len({port for port, _ in _Recorder.seen}) == calls
