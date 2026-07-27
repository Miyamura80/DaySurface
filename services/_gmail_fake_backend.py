"""E2E-only fake Gmail backend.

Returns a stand-in for the ``googleapiclient`` Gmail v1 ``Resource`` that serves
canned, raw-Gmail-API-shaped fixtures instead of talking to Google. This exists
so the MCP-App e2e harness (``.agents/skills/goose-gui-e2e``) can render the
gmail_inbox app in a real host **without** a linked Google account, an OAuth
consent, or any network egress.

Design:
  * The fixtures are shaped like the real ``users().threads().get(format="full")``
    payload, so the *real* service code (``_parse_message_resource`` -> Pydantic
    ``GmailThread``) runs end-to-end. Only the network boundary is faked.
  * The fake ``Resource`` implements the chained calls the Gmail services make on
    two render paths: ``gmail_get_thread`` (the thread reader) and
    ``gmail_curate_inbox`` (the curated inbox list, incl. the batch thread-fetch).
    Both matter because the app falls back to a curated-inbox refresh when the
    host delivers the thread result before the iframe's handler is registered, so
    the scenario must render either way. Unknown methods raise
    ``NotImplementedError`` loudly rather than silently returning junk, so a new
    code path that reaches Google in a faked run fails visibly.

Import vs. activation: like every ``services.*`` submodule, this one IS imported
at startup by ``services.discover_services`` (including in production) - it just
defines cheap fixtures + fake classes (only ``base64``/``typing``), so importing
it is inert. What must never happen in production is *activation*: returning this
fake in place of a real Gmail client. That is gated entirely at the call site
(``services.gmail_svc._maybe_fake_gmail_client``), which returns the fake only
when ``GMAIL_FAKE_BACKEND=1`` and hard-refuses under ``DEV_ENV=prod``. This module
has no way to activate itself.
"""

from __future__ import annotations

import base64
from typing import Any

_DEMO_EMAIL = "you@startup.com"


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


_TERMSHEET_HTML = (
    "<p>Hi,</p>"
    "<p>Great call today. Attaching the final redlines - one open point on the "
    "<b>liquidation preference</b> (we're proposing 1x non-participating).</p>"
    "<p>If that works, we can sign this week.</p>"
    "<p>Best,<br/>Dana</p>"
    # Remote image: exercises the reader's blocked-by-default "Show images"
    # banner. `.invalid` (RFC 2606) never resolves, so a "Show images" click
    # fails the server-side proxy fetch identically offline and in open-egress
    # CI - scenarios can deterministically assert the blocked/Retry states.
    '<img src="https://img.invalid/northwind-logo.png" alt="Northwind Ventures">'
)


def _headers(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in d.items()]


# Raw Gmail API ``threads().get(format="full")`` payloads, keyed by thread id.
# Mirrors the frontend dev-preview fixtures (mcp_server/dev_preview/src/fixtures.ts)
# so the reader renders the same content across preview and e2e.
_THREADS: dict[str, dict[str, Any]] = {
    "t-1001": {
        "id": "t-1001",
        "messages": [
            {
                "id": "m-1",
                "threadId": "t-1001",
                "snippet": "Great call today. Attaching the final redlines...",
                # 2026-07-05T09:14:00Z. _parse_message_resource derives the
                # message date from internalDate (not the Date header), so this
                # must match the Date header + dev-preview fixture year.
                "internalDate": "1783242840000",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": _headers(
                        {
                            "From": "Dana Whitfield <dana@northwind.vc>",
                            "To": _DEMO_EMAIL,
                            "Subject": "Series A term sheet - final redlines",
                            "Date": "Sat, 05 Jul 2026 09:14:00 +0000",
                        }
                    ),
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": _b64url(_TERMSHEET_HTML),
                                "size": len(_TERMSHEET_HTML),
                            },
                        },
                        {
                            "mimeType": "application/pdf",
                            "filename": "termsheet-v7.pdf",
                            "body": {"attachmentId": "att-1", "size": 184320},
                        },
                    ],
                },
            }
        ],
    },
}


# Fake Resource chain. Methods take ``**kwargs`` because they mirror
# googleapiclient's camelCase Gmail API (userId, maxResults, metadataHeaders, ...)
# and only a couple of values matter to the fixtures - keeping the surface liberal
# avoids brittle signatures without renaming anything.


class _Executable:
    """Stand-in for a googleapiclient request object: ``.execute()`` returns data."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._value


class _Threads:
    def get(self, **kwargs: Any) -> _Executable:
        tid = kwargs.get("id")
        # Real Gmail returns 404 for an unknown thread id; synthesizing an empty
        # thread instead would let a misspelled/drifted fixture id pass a render
        # check with no content. Fail loudly, matching this module's contract.
        if tid not in _THREADS:
            raise LookupError(
                f"fake Gmail backend has no fixture thread {tid!r}; add it to "
                "_THREADS in services/_gmail_fake_backend.py"
            )
        return _Executable(_THREADS[tid])

    def list(self, **kwargs: Any) -> _Executable:
        # gmail_curate_inbox lists thread stubs, then batch-fetches each full
        # thread. Serve every fixture thread as an inbox stub.
        return _Executable({"threads": [{"id": tid} for tid in _THREADS]})


class _Drafts:
    def list(self, **kwargs: Any) -> _Executable:
        return _Executable({"drafts": []})

    def get(self, **kwargs: Any) -> _Executable:
        return _Executable({})


class _Labels:
    def list(self, **kwargs: Any) -> _Executable:
        return _Executable({"labels": []})


class _Attachments:
    def get(self, **kwargs: Any) -> _Executable:
        # No fixture path fetches attachment bytes: the sample thread's PDF is
        # never downloaded, and it has no inline cid: images to resolve. Returning
        # empty bytes would mask a real fetch failure, so fail loudly if a new
        # path hits this - add fixture bytes for the id when a scenario needs them.
        raise NotImplementedError(
            f"fake Gmail backend does not serve attachment bytes (id={kwargs.get('id')!r}); "
            "add a fixture in services/_gmail_fake_backend.py if an e2e path needs one"
        )


class _Messages:
    def attachments(self) -> _Attachments:
        return _Attachments()


class _Users:
    def threads(self) -> _Threads:
        return _Threads()

    def drafts(self) -> _Drafts:
        return _Drafts()

    def labels(self) -> _Labels:
        return _Labels()

    def messages(self) -> _Messages:
        return _Messages()

    def getProfile(self, **kwargs: Any) -> _Executable:  # noqa: N802 - mirrors googleapiclient's method name
        return _Executable({"emailAddress": _DEMO_EMAIL})


class _FakeBatch:
    """Fake of googleapiclient's ``BatchHttpRequest``.

    ``gmail_curate_inbox`` queues per-thread ``threads().get()`` requests and
    reads the results in callbacks. Mirror that: run each queued request and
    hand its result (or exception) to its callback, exactly as the real batch
    does - per-item failures go to the callback, never the caller.
    """

    def __init__(self) -> None:
        self._queue: list[tuple[_Executable, Any]] = []

    def add(self, request: _Executable, callback: Any = None) -> None:
        self._queue.append((request, callback))

    def execute(self, *args: Any, **kwargs: Any) -> None:
        for i, (request, callback) in enumerate(self._queue):
            if callback is None:
                continue
            try:
                callback(str(i), request.execute(), None)
            except Exception as exc:  # noqa: BLE001 - mirror the real batch: hand per-item errors to the callback, not the caller
                callback(str(i), None, exc)


class _FakeGmailResource:
    """Minimal fake of the googleapiclient Gmail v1 ``Resource``.

    Implements only the chained calls the Gmail services actually issue for the
    thread-read and curated-inbox render paths. Anything else raises so an
    untested path can't silently pass in a faked run.
    """

    def users(self) -> _Users:
        return _Users()

    def new_batch_http_request(self) -> _FakeBatch:
        return _FakeBatch()

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(
            f"fake Gmail backend does not implement '{name}'; add it to "
            "services/_gmail_fake_backend.py if a new e2e path needs it"
        )


def build_fake_gmail_client() -> _FakeGmailResource:
    """Return a fixture-serving fake Gmail client. E2E-only; see module docstring."""
    return _FakeGmailResource()
