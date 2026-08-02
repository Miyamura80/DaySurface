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
import email
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


# Module-level so drafts survive across the per-call client instances.
_DRAFTS: dict[str, dict[str, Any]] = {}


_DRAFT_HEADERS = (
    "From",
    "To",
    "Cc",
    "Bcc",
    "Subject",
    "Date",
    "In-Reply-To",
    "References",
)


def _mime_to_payload(raw_b64url: str) -> dict[str, Any]:
    """Turn a base64url RFC 5322 message into a ``format=full`` Gmail payload.

    The inverse of what the real service just did, so a draft round-trips
    through the same shape Gmail would return: headers the parser reads, text
    parts carrying base64url bodies, and attachments reduced to an
    ``attachmentId`` + size the way Gmail stores them out-of-line.
    """
    if not raw_b64url:
        return {"mimeType": "text/plain", "headers": [], "body": {"size": 0}}
    # The real builder pads correctly; be liberal anyway so a padding-stripped
    # value cannot fail the whole draft path with a bare binascii error.
    padded = raw_b64url + "=" * (-len(raw_b64url) % 4)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(padded))

    headers = _headers({h: msg[h] for h in _DRAFT_HEADERS if msg[h]})
    parts: list[dict[str, Any]] = []
    att_seq = 0
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        # get_payload(decode=True) is typed as possibly returning a Message for
        # a nested part; walk() already skipped multiparts, so anything else is
        # an empty body rather than something to decode.
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        if filename:
            att_seq += 1
            parts.append(
                {
                    "mimeType": part.get_content_type(),
                    "filename": filename,
                    "body": {"attachmentId": f"fatt-{att_seq}", "size": len(payload)},
                }
            )
        else:
            text = payload.decode("utf-8", errors="replace")
            parts.append(
                {
                    "mimeType": part.get_content_type(),
                    "body": {"data": _b64url(text), "size": len(text)},
                }
            )
    if len(parts) == 1 and not parts[0].get("filename"):
        # Single-part message: Gmail inlines the body rather than wrapping it.
        return {
            "mimeType": parts[0]["mimeType"],
            "headers": headers,
            "body": parts[0]["body"],
        }
    return {"mimeType": msg.get_content_type(), "headers": headers, "parts": parts}


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
    """In-memory draft store backing the compose/reply/send e2e paths.

    Gmail takes a whole RFC 5322 message on ``create``/``update`` (base64url in
    ``message.raw``) and hands back a Gmail-shaped resource on ``get``. The fake
    does the same round trip - parse the MIME the real service built, re-emit it
    in ``format=full`` shape - so the real MIME builder and the real response
    parser both run for real. Faking only the persistence keeps the composer's
    save -> reopen -> send loop honest end to end.

    State lives on the instance the resource hands out, so it survives for the
    life of the server process and resets when it restarts, which is what a
    scenario wants.
    """

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{len(self._store) + 1}"

    def create(self, **kwargs: Any) -> _Executable:
        message = (kwargs.get("body") or {}).get("message") or {}
        draft_id = self._next_id("draft")
        payload = _mime_to_payload(message.get("raw") or "")
        thread_id = message.get("threadId")
        self._store[draft_id] = {
            "id": draft_id,
            "message": {
                "id": self._next_id("dmsg"),
                "threadId": thread_id,
                "labelIds": ["DRAFT"],
                "payload": payload,
            },
        }
        # Real Gmail's create response carries only the minimal message; the
        # service re-reads at format=full, so returning more here would let a
        # missing re-read pass unnoticed.
        return _Executable(
            {
                "id": draft_id,
                "message": {
                    "id": self._store[draft_id]["message"]["id"],
                    "threadId": thread_id,
                },
            }
        )

    def update(self, **kwargs: Any) -> _Executable:
        draft_id = kwargs.get("id") or ""
        existing = self._store.get(draft_id)
        if existing is None:
            raise LookupError(f"fake Gmail backend has no draft {draft_id!r}")
        message = (kwargs.get("body") or {}).get("message") or {}
        # Whole-message replace, exactly as Gmail does.
        existing["message"]["payload"] = _mime_to_payload(message.get("raw") or "")
        return _Executable(
            {"id": draft_id, "message": {"id": existing["message"]["id"]}}
        )

    def get(self, **kwargs: Any) -> _Executable:
        draft_id = kwargs.get("id") or ""
        draft = self._store.get(draft_id)
        if draft is None:
            raise LookupError(
                f"fake Gmail backend has no draft {draft_id!r}; it was never created, "
                "or was already sent or discarded"
            )
        return _Executable(draft)

    def send(self, **kwargs: Any) -> _Executable:
        draft_id = (kwargs.get("body") or {}).get("id") or ""
        draft = self._store.pop(draft_id, None)
        if draft is None:
            raise LookupError(f"fake Gmail backend has no draft {draft_id!r} to send")
        msg = draft["message"]
        return _Executable(
            {"id": msg["id"], "threadId": msg.get("threadId"), "labelIds": ["SENT"]}
        )

    def delete(self, **kwargs: Any) -> _Executable:
        self._store.pop(kwargs.get("id") or "", None)
        return _Executable(None)

    def list(self, **kwargs: Any) -> _Executable:
        return _Executable(
            {
                "drafts": [
                    {"id": d["id"], "message": d["message"]}
                    for d in self._store.values()
                ]
            }
        )


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
    def __init__(self, drafts_store: dict[str, dict[str, Any]]) -> None:
        self._drafts_store = drafts_store

    def threads(self) -> _Threads:
        return _Threads()

    def drafts(self) -> _Drafts:
        return _Drafts(self._drafts_store)

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

    def __init__(self) -> None:
        # One store per client instance. _maybe_fake_gmail_client builds a client
        # per call, so keep the drafts module-level: a draft created by
        # gmail_reply_to_thread must still be there when the app's save/send
        # tools arrive on later requests.
        self._drafts_store = _DRAFTS

    def users(self) -> _Users:
        return _Users(self._drafts_store)

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
