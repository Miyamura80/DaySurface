"""Tests for the RFC 9745 / RFC 8594 deprecation header dependency."""

from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api_server.deprecation import DEPRECATION_POLICY_URL, deprecate
from tests.test_template import TestTemplate


def _app_with_route(**kwargs) -> FastAPI:
    app = FastAPI()

    @app.get("/legacy", dependencies=[Depends(deprecate(**kwargs))], deprecated=True)
    def legacy():
        return {"ok": True}

    return app


class TestDeprecationHeaders(TestTemplate):
    def test_defaults_to_sf_date_when_no_since(self):
        # RFC 9745 has no bare token, so an unspecified `since` still yields an
        # sf-date (defaulted to declaration time), never "true".
        resp = TestClient(_app_with_route()).get("/legacy")
        assert resp.status_code == 200
        assert resp.headers["Deprecation"].startswith("@")
        assert int(resp.headers["Deprecation"].lstrip("@")) > 0
        assert "Sunset" not in resp.headers
        assert 'rel="deprecation"' in resp.headers["Link"]
        assert DEPRECATION_POLICY_URL in resp.headers["Link"]

    def test_since_emitted_as_sf_date(self):
        since = datetime(2025, 10, 1, tzinfo=UTC)
        resp = TestClient(_app_with_route(since=since)).get("/legacy")
        assert resp.headers["Deprecation"] == f"@{int(since.timestamp())}"

    def test_naive_since_treated_as_utc(self):
        # A naive datetime must be read as UTC, not the server's local zone.
        naive = datetime(2025, 10, 1, 0, 0, 0)
        aware = datetime(2025, 10, 1, 0, 0, 0, tzinfo=UTC)
        resp = TestClient(_app_with_route(since=naive)).get("/legacy")
        assert resp.headers["Deprecation"] == f"@{int(aware.timestamp())}"

    def test_sunset_emitted_as_http_date(self):
        sunset = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
        resp = TestClient(_app_with_route(sunset=sunset)).get("/legacy")
        assert resp.headers["Sunset"] == "Wed, 31 Dec 2025 23:59:59 GMT"

    def test_custom_policy_url(self):
        url = "https://example.com/policy"
        resp = TestClient(_app_with_route(policy_url=url)).get("/legacy")
        assert resp.headers["Link"] == f'<{url}>; rel="deprecation"'

    def test_marked_deprecated_in_openapi(self):
        app = _app_with_route()
        spec = app.openapi()
        assert spec["paths"]["/legacy"]["get"]["deprecated"] is True
