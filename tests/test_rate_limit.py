"""Tests for rate limiting middleware."""

import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_server.middleware.rate_limit import RateLimitMiddleware
from tests.test_template import TestTemplate


def _make_app(tier_limits=None):
    """Create a minimal FastAPI app with rate limiting."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)  # type: ignore[arg-type]

    @app.get("/test")
    def test_endpoint():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


class TestRateLimit(TestTemplate):
    def setup_method(self):
        """Disable TESTING bypass so rate limiting is exercised."""
        self._orig_testing = os.environ.pop("TESTING", None)

    def teardown_method(self):
        if self._orig_testing is not None:
            os.environ["TESTING"] = self._orig_testing

    @patch("api_server.middleware.rate_limit._get_tier_limits")
    def test_burst_triggers_429(self, mock_limits):
        """Exceeding per-second burst limit should return 429."""
        mock_limits.return_value = {"rps": 1, "rpm": 1000, "rph": 10000, "rpd": 100000}
        app = _make_app()
        client = TestClient(app)
        # Use X-API-KEY so identity is not "ip:testclient" (which is exempt)
        headers = {"X-API-KEY": "test-rate-limit-key"}

        # First request should succeed
        resp = client.get("/test", headers=headers)
        assert resp.status_code == 200

        # Second request within same second should be rate limited
        resp2 = client.get("/test", headers=headers)
        assert resp2.status_code == 429
        assert "Retry-After" in resp2.headers

    @patch("api_server.middleware.rate_limit._get_tier_limits")
    def test_headers_present_on_success(self, mock_limits):
        mock_limits.return_value = {
            "rps": 100,
            "rpm": 1000,
            "rph": 10000,
            "rpd": 100000,
        }
        app = _make_app()
        client = TestClient(app)
        headers = {"X-API-KEY": "test-headers-key"}

        resp = client.get("/test", headers=headers)
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers
        assert "RateLimit" in resp.headers
        assert "RateLimit-Policy" in resp.headers

    @patch("api_server.middleware.rate_limit._get_tier_limits")
    def test_exempt_paths_bypass(self, mock_limits):
        """Health endpoint should bypass rate limiting."""
        mock_limits.return_value = {"rps": 1, "rpm": 1, "rph": 1, "rpd": 1}
        app = _make_app()
        client = TestClient(app)

        # Multiple requests to /health should all succeed
        for _ in range(5):
            resp = client.get("/health")
            assert resp.status_code == 200

    @patch("api_server.middleware.rate_limit._get_tier_limits")
    def test_429_error_body(self, mock_limits):
        """429 response should have structured error body."""
        mock_limits.return_value = {"rps": 1, "rpm": 1000, "rph": 10000, "rpd": 100000}
        app = _make_app()
        client = TestClient(app)
        headers = {"X-API-KEY": "test-error-body-key"}

        client.get("/test", headers=headers)  # Use up the limit
        resp = client.get("/test", headers=headers)
        assert resp.status_code == 429

        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "rate_limited"
        assert "Retry" in body["error"]["message"]
