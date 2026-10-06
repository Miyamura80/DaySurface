"""Reply drafting: ``gmail_reply_to_thread`` and its recipient/threading policy.

Split from ``gmail_drafts_svc`` (which keeps the draft CRUD) because replying
carries its own policy: who the reply goes to, the In-Reply-To / References
chain, and refusing a reply the user already sent from the composer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import formataddr
from typing import Any

from loguru import logger as log

from models.curation import CurationState
from models.gmail import GmailDraft, GmailReplyInput
from services import ClientRefusalError, service
from services.curation_ledger import mark_state_best_effort
from services.gmail_draft_helpers import _fetch_draft_model, _inputs_to_uploads
from services.gmail_message_roles import is_draft, may_be_incoming
from services.gmail_messages_svc import _internal_date_to_dt
from services.gmail_svc import (
    _account_email,
    _addresses,
    _build_raw_message,
    _get_gmail_client,
    _headers_to_dict,
)

# How far the model can plausibly lag a composer send. An own reply older than
# this is one the user may well want to follow up on.
_RECENT_REPLY = timedelta(minutes=10)


def _headers(msg: dict[str, Any]) -> dict[str, str]:
    return _headers_to_dict((msg.get("payload") or {}).get("headers"))


def _emails(msg: dict[str, Any], *fields: str) -> set[str]:
    headers = _headers(msg)
    return {addr.lower() for f in fields for _, addr in _addresses(headers.get(f))}


def _select_reply_recipient(
    messages: list[dict[str, Any]], self_email: str | None
) -> str:
    """Choose the default ``To`` for a reply to ``messages`` (thread order).

    A reply should reach the other party, not the account owner. Walk the
    thread newest-first and reply to the sender (``Reply-To`` falling back to
    ``From``, RFC 5322 5.2.2) of the most recent message NOT sent by the owner.
    If every message was sent by the owner - e.g. they sent the last message
    and are now following up - fall back to the recipients (``To`` + ``Cc``) of
    the latest message with the owner removed, so the reply still addresses the
    people in the conversation rather than the owner themselves.

    When ``self_email`` is unknown (None) no address matches "self", so this
    reduces to the historical behavior: reply to the last message's sender.
    """
    self_norm = self_email.strip().lower() if self_email else None

    def _is_self(addr: str) -> bool:
        return self_norm is not None and addr.strip().lower() == self_norm

    for msg in reversed(messages):
        headers = _headers(msg)
        # Ownership is decided by ``From`` - who actually sent the message - not
        # ``Reply-To``. An owner-sent message may carry a non-self ``Reply-To``
        # (e.g. "reply to my assistant"); it must still count as the owner's so
        # the loop skips past it to the real other party. Messages with no
        # attributable sender are skipped too.
        from_addresses = _addresses(headers.get("from"))
        if not from_addresses or all(_is_self(addr) for _, addr in from_addresses):
            continue
        # Incoming message: reply to the address its sender asked for - the
        # ``Reply-To`` if present (RFC 5322 5.2.2), else ``From`` - verbatim.
        return headers.get("reply-to") or headers.get("from") or ""

    # Every message in the thread was sent by the account owner: reply to the
    # people the latest message was addressed to (To + Cc), minus self.
    last_headers = _headers(messages[-1])
    recipients: list[str] = []
    seen: set[str] = set()
    for field in ("to", "cc"):
        for name, addr in _addresses(last_headers.get(field)):
            key = addr.lower()
            if _is_self(addr) or key in seen:
                continue
            seen.add(key)
            recipients.append(formataddr((name, addr)))
    return ", ".join(recipients)


class DuplicateReplyError(ClientRefusalError):
    """The user just replied to this thread themselves (HTTP 409)."""

    http_status = 409


def _refuse_duplicate_reply(
    thread_id: str, messages: list[dict[str, Any]], now: datetime
) -> None:
    """Raise when the user's own reply to the latest sender just went out.

    The composer sends through app-only tools the model never sees, so a model
    asked to reply can be a step behind the user. Only that case is refused:
    the user's message must be the newest, sent within ``_RECENT_REPLY``, and
    addressed to whoever wrote the latest incoming message. An older reply, a
    forward to someone else, or a thread with no incoming mail all pass.
    """
    sent = [m for m in messages if not is_draft(m)]
    incoming = [m for m in sent if may_be_incoming(m)]
    if not sent or not incoming or may_be_incoming(sent[-1]):
        return
    reply = sent[-1]
    at = _internal_date_to_dt(reply.get("internalDate"))
    if at is None or now - at > _RECENT_REPLY:
        return
    reached = _emails(reply, "to", "cc", "bcc")
    if not reached & _emails(incoming[-1], "reply-to", "from"):
        return
    minutes = int((now - at).total_seconds() // 60)
    raise DuplicateReplyError(
        f"The user replied to thread {thread_id!r} {minutes} min ago "
        f"({at:%H:%M} UTC), to {', '.join(sorted(reached))}; their reply is the "
        "newest message, probably sent from the composer. Don't draft another "
        "reply. If the user asked for a follow-up, call again with follow_up=true."
    )


@service(
    name="gmail_reply_to_thread",
    description="Create a reply draft on an existing Gmail thread. ALWAYS use this tool instead of composing reply text in chat - it creates a real Gmail draft and opens an interactive composer UI where the user can review, edit, and send. Pass your drafted reply in the 'body' parameter. Recipients are yours to control: pass 'to', 'cc', and/or 'bcc' (each a comma-separated address list) to set them explicitly. If you omit 'to', it defaults to the other party in the thread (never the account owner); omitted 'cc'/'bcc' are left unset. If every message in the thread is yours (no other participant to reply to), you must pass 'to' explicitly or the call errors. If the user replied to the latest sender in the last few minutes (e.g. from the composer), the call errors instead of drafting a duplicate; pass follow_up=true only when the user asked for a follow-up. When an interactive UI is rendered alongside the result, keep your text response brief since the user can edit in the UI.",
    input_model=GmailReplyInput,
    output_model=GmailDraft,
    mutating=True,
)
def gmail_reply_to_thread(input: GmailReplyInput) -> GmailDraft:
    """Create a reply draft attached to the given thread.

    Recipients are caller-controlled: ``to``/``cc``/``bcc`` are used verbatim
    when supplied. When ``to`` is omitted it defaults to the sender (``Reply-To``
    falling back to ``From``, RFC 5322 5.2.2) of the most recent message the
    account owner did NOT send, so a bare reply reaches the other party rather
    than the owner's own address. If ``to`` is omitted and the thread has no
    other participant to derive one from (every message is the owner's), raises
    ``ValueError`` rather than creating a blank-``To`` draft - pass ``to``
    explicitly for such threads. Prefixes the subject with ``Re:`` unless the
    originating subject already starts with ``Re:``. Propagates the parent's
    ``Message-ID`` as ``In-Reply-To`` and appends to ``References`` so non-Gmail
    MUAs also thread the conversation; Gmail itself uses the ``threadId`` on the
    API wrapper.
    """
    svc = _get_gmail_client(input.user_id)
    thread = (
        svc.users()
        .threads()
        .get(userId="me", id=input.thread_id, format="metadata")
        .execute()
    )
    # Gmail lists a thread's messages oldest first, but "the latest message"
    # drives the guard, the default recipient and the threading headers, so
    # don't rely on it.
    messages = sorted(
        thread.get("messages") or [], key=lambda m: int(m.get("internalDate") or 0)
    )
    if not messages:
        raise ValueError(f"Thread {input.thread_id!r} has no messages to reply to")
    if not input.follow_up:
        _refuse_duplicate_reply(input.thread_id, messages, datetime.now(UTC))
    headers = _headers(messages[-1])
    # Caller-supplied recipients win; only compute the default (and pay the
    # token-row lookup) when the caller left ``to`` unset.
    if input.to is not None:
        to = input.to
    else:
        to = _select_reply_recipient(messages, _account_email(input.user_id))
        if not to:
            # Every message is from the owner and the thread names no other
            # participant, so there is nobody to reply to. Fail clearly instead
            # of creating a draft with a blank (malformed) To header.
            raise ValueError(
                f"Cannot determine a reply recipient for thread "
                f"{input.thread_id!r}: every message is from you and the thread "
                "has no other participants. Pass 'to' explicitly."
            )
    orig_subject = headers.get("subject") or ""
    if input.subject is not None:
        subject = input.subject
    elif orig_subject.lower().startswith("re:"):
        subject = orig_subject
    else:
        subject = f"Re: {orig_subject}" if orig_subject else "Re:"
    body = input.body if input.body is not None else ""

    parent_message_id = headers.get("message-id")
    parent_references = headers.get("references")
    in_reply_to = parent_message_id
    if parent_message_id and parent_references:
        references = f"{parent_references} {parent_message_id}"
    else:
        references = parent_references or parent_message_id

    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        cc=input.cc,
        bcc=input.bcc,
        in_reply_to_thread_id=input.thread_id,
        in_reply_to=in_reply_to,
        references=references,
        attachments=_inputs_to_uploads(input.attachments),
    )
    created = (
        svc.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": raw, "threadId": input.thread_id}},
        )
        .execute()
    )
    log.debug("Created Gmail reply draft id={}", created.get("id"))
    # A reply draft is an action on the thread: reflect it in the ledger so the
    # triage view shows the thread as handled (with its draft) rather than still
    # needing a reply. Best-effort - a DB hiccup must not fail draft creation.
    mark_state_best_effort(
        input.user_id,
        input.thread_id,
        CurationState.acted,
        draft_id=created.get("id"),
    )
    # Re-fetch at format=full: the create response omits the saved recipients,
    # subject, and body, so echoing it directly would return all-null.
    return _fetch_draft_model(svc, created.get("id") or "")
