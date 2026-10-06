"""Rate-limit backoff on every Gmail API request (services._gmail_retry).

Driven through a real discovery-built Gmail client over ``HttpMockSequence``,
so the request builder is exercised the way production wires it.
"""

import json
import math
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from services import _gmail_retry, gmail_svc, upstream_write_scope
from services._gmail_retry import GmailRateLimitedError, RateLimitRetryingRequest
from services.gmail_curate_svc import _batch_get_threads
from tests.test_template import TestTemplate

_OK = ({"status": "200"}, json.dumps({"threads": [{"id": "t1"}]}))
# The body Gmail actually returned on the production 403 that prompted this.
_QUOTA_403 = (
    {"status": "403"},
    json.dumps(
        {
            "error": {
                "code": 403,
                "message": "Quota exceeded for quota metric 'Total Query Cost' "
                "for consumer 'project_number:123'.",
                "errors": [
                    {
                        "message": "Quota exceeded",
                        "domain": "usageLimits",
                        "reason": "rateLimitExceeded",
                    }
                ],
            }
        }
    ),
)
_PERMISSION_403 = (
    {"status": "403"},
    json.dumps(
        {
            "error": {
                "code": 403,
                "message": "Insufficient Permission",
                "errors": [{"reason": "insufficientPermissions"}],
            }
        }
    ),
)


def _client(responses):
    return build(
        "gmail",
        "v1",
        http=HttpMockSequence(responses),
        requestBuilder=RateLimitRetryingRequest,
        static_discovery=True,
    )


def _list(client):
    return client.users().threads().list(userId="me").execute()


@pytest.fixture
def sleeps():
    with patch.object(_gmail_retry.time, "sleep") as sleep:
        yield sleep


class TestGmailRateLimitRetry(TestTemplate):
    def test_rate_limited_403_is_retried_until_it_clears(self, sleeps):
        client = _client([_QUOTA_403, _QUOTA_403, _OK])
        assert _list(client)["threads"] == [{"id": "t1"}]
        assert sleeps.call_count == 2

    def test_429_is_retried(self, sleeps):
        client = _client([({"status": "429"}, "{}"), _OK])
        assert _list(client)["threads"] == [{"id": "t1"}]
        assert sleeps.call_count == 1

    def test_exhausted_retries_raise_a_clean_error(self, sleeps):
        client = _client([_QUOTA_403] * (_gmail_retry._MAX_RETRIES + 1))
        with pytest.raises(GmailRateLimitedError) as info:
            _list(client)
        # The host sees recovery advice, never Google's raw error body.
        assert "project_number" not in str(info.value)
        assert "retry" in str(info.value)
        assert isinstance(info.value.__cause__, HttpError)
        assert sleeps.call_count == _gmail_retry._MAX_RETRIES

    def test_other_403s_are_not_retried(self, sleeps):
        client = _client([_PERMISSION_403])
        with pytest.raises(HttpError) as info:
            _list(client)
        assert info.value.resp.status == 403
        sleeps.assert_not_called()

    def test_5xx_is_not_retried(self, sleeps):
        # A 5xx on messages.send may already have delivered the mail, so only
        # rejections Gmail refuses up front are safe to repeat.
        client = _client([({"status": "500"}, "{}"), _OK])
        with pytest.raises(HttpError) as info:
            _list(client)
        assert info.value.resp.status == 500
        sleeps.assert_not_called()

    def test_retry_after_header_is_honored_and_capped(self, sleeps):
        client = _client(
            [
                ({"status": "429", "retry-after": "3"}, "{}"),
                ({"status": "429", "retry-after": "999"}, "{}"),
                _OK,
            ]
        )
        _list(client)
        first, second = (c.args[0] for c in sleeps.call_args_list)
        # Jitter only stretches the wait: never earlier than Gmail asked.
        assert 3.0 <= first <= 3.0 * 1.25
        cap = _gmail_retry._MAX_RETRY_AFTER_S
        assert cap <= second <= cap * 1.25

    def test_retry_after_http_date_is_honored(self, sleeps):
        when = datetime.now(UTC) + timedelta(seconds=10)
        client = _client(
            [
                (
                    {
                        "status": "429",
                        "retry-after": format_datetime(when, usegmt=True),
                    },
                    "{}",
                ),
                _OK,
            ]
        )
        _list(client)
        # The date drops sub-seconds and setup time elapses, so only bound it
        # well clear of the first backoff step (at most 1.25 s).
        assert 5.0 < sleeps.call_args.args[0] <= 10.0 * 1.25

    def test_unusable_retry_after_falls_back_to_backoff(self, sleeps):
        # "nan" parses as a float; time.sleep(nan) would raise mid-retry.
        client = _client(
            [
                ({"status": "429", "retry-after": "nan"}, "{}"),
                ({"status": "429", "retry-after": "soon"}, "{}"),
                # Out-of-range date: parsedate_to_datetime raises OverflowError.
                (
                    {
                        "status": "429",
                        "retry-after": "Mon, 01 Jan 99999999999999999999 00:00:00 GMT",
                    },
                    "{}",
                ),
                _OK,
            ]
        )
        _list(client)
        delays = [c.args[0] for c in sleeps.call_args_list]
        assert len(delays) == 3
        assert all(math.isfinite(d) and 0 < d <= 5.0 for d in delays)

    def test_backoff_grows(self, sleeps):
        client = _client([_QUOTA_403] * 3 + [_OK])
        _list(client)
        delays = [c.args[0] for c in sleeps.call_args_list]
        assert delays[0] < delays[1] < delays[2]

    def test_gmail_client_is_built_with_the_retrying_request(self):
        # Every service gets the backoff only because the client is built with
        # it; guard the wiring, not just the class.
        with (
            patch(
                "googleapiclient.discovery.build", return_value=object()
            ) as fake_build,
            patch.object(gmail_svc, "_cached_client", return_value=None),
            patch.object(gmail_svc, "_store_client"),
            patch.object(gmail_svc, "_mint_access_token", return_value="at"),
            patch("common.token_encryption.require_encryption") as require_encryption,
            patch.object(gmail_svc, "_get_db_session") as get_session,
            patch.object(gmail_svc, "_load_token_row") as load_row,
        ):
            require_encryption.return_value.decrypt.return_value = "rt"
            get_session.return_value.__enter__.return_value = object()
            load_row.return_value.refresh_token_enc = b"enc"
            gmail_svc._get_gmail_client("alice")

        assert fake_build.call_args.kwargs["requestBuilder"] is RateLimitRetryingRequest

    def test_rate_limit_reports_whether_a_write_already_landed(self, sleeps):
        # A draft created, then its re-read refused: the write may stand, so
        # the error must not let idempotency treat the call as never run.
        created = ({"status": "200"}, json.dumps({"id": "d1"}))
        refused = [_QUOTA_403] * (_gmail_retry._MAX_RETRIES + 1)
        client = _client([created, *refused])
        with upstream_write_scope():
            client.users().drafts().create(userId="me", body={}).execute()
            with pytest.raises(GmailRateLimitedError) as info:
                client.users().drafts().get(userId="me", id="d1").execute()
        assert info.value.side_effects_possible is True

    def test_rate_limit_before_any_write_reports_none(self, sleeps):
        client = _client([_QUOTA_403] * (_gmail_retry._MAX_RETRIES + 1))
        with upstream_write_scope(), pytest.raises(GmailRateLimitedError) as info:
            client.users().drafts().create(userId="me", body={}).execute()
        assert info.value.side_effects_possible is False


def _http_error(status: int, content: bytes = b"{}") -> HttpError:
    return HttpError(
        resp=MagicMock(status=status, get=lambda *_: None), content=content
    )


class _ScriptedBatch:
    """Batch stand-in: ``outcome(tid)`` decides each sub-request's result."""

    def __init__(self, outcome, refuse_whole=None):
        self._outcome = outcome
        self._refuse_whole = refuse_whole
        self._calls: list = []

    def __call__(self, callback):
        # Stands in for new_batch_http_request(callback=...).
        self._callback = callback
        return self

    def add(self, req, request_id):
        self._calls.append((req, request_id))

    def execute(self):
        if self._refuse_whole is not None:
            raise self._refuse_whole
        for _req, tid in self._calls:
            result = self._outcome(tid)
            if isinstance(result, Exception):
                self._callback(tid, None, result)
            else:
                self._callback(tid, result, None)


def _batch_svc(batches):
    """Fake client whose successive batch requests come from ``batches``."""
    pending = iter(batches)
    svc = MagicMock(
        new_batch_http_request=lambda callback: next(pending)(callback),
    )
    svc.users().threads().get = MagicMock(side_effect=lambda **kw: MagicMock(kwargs=kw))
    return svc


_RATE_LIMITED = _http_error(403, _QUOTA_403[1].encode())


class TestBatchRateLimitRetry(TestTemplate):
    def test_refused_sub_requests_are_retried(self, sleeps):
        refused_once = {"t2"}

        def first(tid):
            return _RATE_LIMITED if tid in refused_once else {"id": tid}

        retry = _ScriptedBatch(lambda tid: {"id": tid})
        svc = _batch_svc([_ScriptedBatch(first), retry])
        got = _batch_get_threads(svc, ["t1", "t2"], fmt="minimal")
        assert set(got) == {"t1", "t2"}
        assert sleeps.call_count == 1
        # Only the refused thread went out again.
        assert [tid for _, tid in retry._calls] == ["t2"]

    def test_persistent_refusal_raises_instead_of_a_partial_result(self, sleeps):
        batches = [
            _ScriptedBatch(lambda tid: _RATE_LIMITED)
            for _ in range(_gmail_retry._MAX_RETRIES + 1)
        ]
        with pytest.raises(GmailRateLimitedError):
            _batch_get_threads(_batch_svc(batches), ["t1"], fmt="minimal")
        assert sleeps.call_count == _gmail_retry._MAX_RETRIES

    def test_other_sub_request_errors_are_skipped_without_retry(self, sleeps):
        svc = _batch_svc([_ScriptedBatch(lambda tid: _http_error(404))])
        assert _batch_get_threads(svc, ["gone"], fmt="minimal") == {}
        sleeps.assert_not_called()

    def test_refused_batch_request_is_retried_whole(self, sleeps):
        svc = _batch_svc(
            [
                _ScriptedBatch(None, refuse_whole=_http_error(429)),
                _ScriptedBatch(lambda tid: {"id": tid}),
            ]
        )
        assert set(_batch_get_threads(svc, ["t1", "t2"], fmt="minimal")) == {
            "t1",
            "t2",
        }
        assert sleeps.call_count == 1

    def test_failed_batch_request_is_not_retried(self, sleeps):
        svc = _batch_svc([_ScriptedBatch(None, refuse_whole=_http_error(500))])
        with pytest.raises(HttpError):
            _batch_get_threads(svc, ["t1"], fmt="minimal")
        sleeps.assert_not_called()

    def test_refused_batch_holds_back_the_remaining_chunks(self, sleeps):
        ids = [f"t{i}" for i in range(120)]  # three chunks of up to 50
        first = _ScriptedBatch(None, refuse_whole=_http_error(429))
        retries = [_ScriptedBatch(lambda tid: {"id": tid}) for _ in range(3)]
        got = _batch_get_threads(_batch_svc([first, *retries]), ids, fmt="minimal")
        assert set(got) == set(ids)
        # Nothing more was sent until the backoff, then all of it once.
        assert sleeps.call_count == 1
        assert [len(b._calls) for b in retries] == [50, 50, 20]

    def test_duplicate_ids_are_requested_once(self, sleeps):
        batch = _ScriptedBatch(lambda tid: {"id": tid})
        _batch_get_threads(_batch_svc([batch]), ["t1", "t1"], fmt="minimal")
        # A batch rejects a repeated request id outright.
        assert [tid for _, tid in batch._calls] == ["t1"]
