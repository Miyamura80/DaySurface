"""Tests for error handler middleware: structured errors, request IDs, Stripe sanitization."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api_server.middleware.error_handler import (
    ErrorHandlerMiddleware,
    RequestIdMiddleware,
)
from tests.test_template import TestTemplate


def _make_app():
    app = FastAPI()
    app.add_middleware(ErrorHandlerMiddleware)  # type: ignore[arg-type]
    app.add_middleware(RequestIdMiddleware)  # type: ignore[arg-type]

    @app.get("/ok")
    def ok():
        return {"status": "ok"}

    @app.get("/fail")
    def fail():
        raise HTTPException(status_code=400, detail="Bad input")

    @app.get("/crash")
    def crash():
        raise RuntimeError("unexpected")

    @app.get("/stripe-error")
    def stripe_error():
        # Simulate a Stripe-like error
        class FakeStripeError(Exception):
            pass

        FakeStripeError.__module__ = "stripe.error"
        raise FakeStripeError("card declined")

    return app


class TestErrorHandler(TestTemplate):
    def test_success_passes_through(self):
        client = TestClient(_make_app())
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_request_id_header(self):
        client = TestClient(_make_app())
        resp = client.get("/ok")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_preserved(self):
        client = TestClient(_make_app())
        resp = client.get("/ok", headers={"X-Request-ID": "my-req-123"})
        assert resp.headers["X-Request-ID"] == "my-req-123"

    def test_structured_error_format(self):
        client = TestClient(_make_app())
        resp = client.get("/fail")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "bad_request"
        assert body["error"]["message"] == "Bad input"
        assert "request_id" in body["error"]

    def test_unhandled_exception_returns_500(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/crash")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal_error"
        assert body["error"]["message"] == "Internal server error"

    def test_stripe_error_sanitized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/stripe-error")
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"]["message"] == "Payment processing error"
        assert "card declined" not in str(body)
