"""Tests for the sign_out service (WorkOS session revocation)."""

import json
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from common import global_config
from models.account import SignOutInput
from services import ClientRefusalError, RetryLaterError, get_registry
from services.account import sign_out_svc
from services.account.sign_out_svc import sign_out
from tests.test_template import TestTemplate

_REAL_CLIENT = httpx.Client
_API_KEY = "sk_test_signout"


def _session(session_id: str, status: str = "active") -> dict:
    return {"object": "session", "id": session_id, "status": status}


class _FakeWorkOS:
    """Records requests and answers them from canned list pages.

    ``override`` replaces the canned behaviour for every request, for tests
    that need a single fixed response or a transport failure.
    """

    def __init__(
        self,
        pages: list[dict],
        revoke_status: Callable[[str], int] = lambda _sid: 200,
        override: Callable[[httpx.Request], httpx.Response] | None = None,
    ):
        self.pages = pages
        self.revoke_status = revoke_status
        self.override = override
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            return self.override(request)
        if request.method == "GET":
            index = int(request.url.params.get("after", "0") or 0)
            return httpx.Response(200, json=self.pages[index])
        sid = json.loads(request.content)["session_id"]
        return httpx.Response(self.revoke_status(sid), json={})

    def client_factory(self, **kwargs):
        return _REAL_CLIENT(transport=httpx.MockTransport(self.handler), **kwargs)

    @property
    def revoked_ids(self) -> list[str]:
        return [
            json.loads(r.content)["session_id"]
            for r in self.requests
            if r.method == "POST"
        ]


def _page(sessions: list[dict], after: str | None = None) -> dict:
    return {
        "object": "list",
        "data": sessions,
        "list_metadata": {"before": None, "after": after},
    }


class TestSignOut(TestTemplate):
    def _run(self, fake: _FakeWorkOS, user_id: str = "user_01ABC"):
        with (
            patch.object(global_config, "WORKOS_API_KEY", _API_KEY),
            patch.object(sign_out_svc.httpx, "Client", side_effect=fake.client_factory),
        ):
            return sign_out(SignOutInput(user_id=user_id))

    def test_revokes_every_active_session_across_pages(self):
        fake = _FakeWorkOS(
            [
                _page([_session("session_a"), _session("session_b")], after="1"),
                _page([_session("session_c")]),
            ]
        )
        result = self._run(fake)

        assert result.sessions_revoked == 3
        assert fake.revoked_ids == ["session_a", "session_b", "session_c"]
        listing = [r for r in fake.requests if r.method == "GET"]
        assert listing[0].url.path == "/user_management/users/user_01ABC/sessions"
        assert listing[0].url.params["limit"] == "100"
        assert listing[1].url.params["after"] == "1"
        for request in fake.requests:
            assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
            assert request.url.host == "api.workos.com"

    def test_skips_sessions_that_are_not_active(self):
        fake = _FakeWorkOS(
            [_page([_session("session_live"), _session("session_old", "revoked")])]
        )
        assert self._run(fake).sessions_revoked == 1
        assert fake.revoked_ids == ["session_live"]

    def test_unknown_workos_user_ends_nothing(self):
        fake = _FakeWorkOS([], override=lambda _request: httpx.Response(404, json={}))
        assert self._run(fake).sessions_revoked == 0
        assert fake.revoked_ids == []

    def test_session_already_gone_is_not_an_error(self):
        fake = _FakeWorkOS(
            [_page([_session("session_a"), _session("session_gone")])],
            revoke_status=lambda sid: 404 if sid == "session_gone" else 200,
        )
        assert self._run(fake).sessions_revoked == 1

    def test_rate_limit_mid_revoke_asks_for_retry_and_flags_partial_effect(self):
        fake = _FakeWorkOS(
            [_page([_session("session_a"), _session("session_b")])],
            revoke_status=lambda sid: 429 if sid == "session_b" else 200,
        )
        with pytest.raises(RetryLaterError) as excinfo:
            self._run(fake)
        assert excinfo.value.side_effects_possible is True

    def test_network_error_before_any_revoke_asks_for_retry(self):
        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("unreachable", request=request)

        fake = _FakeWorkOS([], override=broken)
        with pytest.raises(RetryLaterError) as excinfo:
            self._run(fake)
        assert excinfo.value.side_effects_possible is False

    def test_rejected_api_key_raises_instead_of_reporting_success(self):
        fake = _FakeWorkOS(
            [], override=lambda request: httpx.Response(401, json={}, request=request)
        )
        with pytest.raises(httpx.HTTPStatusError):
            self._run(fake)

    def test_missing_api_key_fails_before_any_request(self):
        fake = _FakeWorkOS([_page([_session("session_a")])])
        with (
            patch.object(global_config, "WORKOS_API_KEY", None),
            patch.object(sign_out_svc.httpx, "Client", side_effect=fake.client_factory),
            pytest.raises(RuntimeError, match="WORKOS_API_KEY"),
        ):
            sign_out(SignOutInput(user_id="user_01ABC"))
        assert fake.requests == []

    def test_blank_user_is_refused(self):
        with pytest.raises(ClientRefusalError):
            sign_out(SignOutInput(user_id=""))

    def test_user_id_is_escaped_in_the_path(self):
        fake = _FakeWorkOS([_page([])])
        self._run(fake, user_id="user_01/../admin")
        assert fake.requests[0].url.raw_path.startswith(
            b"/user_management/users/user_01%2F..%2Fadmin/sessions"
        )

    def test_registered_as_a_mutating_service(self):
        entry = next(e for e in get_registry() if e.name == "sign_out")
        assert entry.mutating is True
