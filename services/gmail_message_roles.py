"""Who a Gmail message is from, read off its labels alone.

A leaf module (no Gmail calls, no DB) so triage freshness
(``curation_status``) and reply drafting (``gmail_reply_svc``) share one
definition of "incoming" without either importing the other.

An *incoming* message is one someone else sent, or one the user sent to
themselves (it lands in INBOX too). The user's own replies and drafts are not.
"""

from __future__ import annotations

from typing import Any

INBOX_LABEL_ID = "INBOX"
SENT_LABEL_ID = "SENT"
DRAFT_LABEL_ID = "DRAFT"
# Category tabs ``build_curate_query()`` excludes with ``-category:...``.
EXCLUDED_CATEGORY_IDS = frozenset(
    {"CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
)


def _labels(msg: dict[str, Any]) -> set[str]:
    return set(msg.get("labelIds") or [])


def is_draft(msg: dict[str, Any]) -> bool:
    return DRAFT_LABEL_ID in _labels(msg)


def may_be_incoming(msg: dict[str, Any]) -> bool:
    """Not a draft, and not the user's own outgoing mail.

    On its own this judges history records: a message can leave a category
    tab after it arrives, so the labels it was added with can't rule it out.
    """
    labels = _labels(msg)
    if DRAFT_LABEL_ID in labels:
        return False
    return SENT_LABEL_ID not in labels or INBOX_LABEL_ID in labels


def is_incoming(msg: dict[str, Any]) -> bool:
    """A message someone else sent, or one the user sent to themselves."""
    # Category-tab mail is outside triage (see curation_status.is_triageable),
    # so it can't make a verdict stale.
    return may_be_incoming(msg) and not _labels(msg) & EXCLUDED_CATEGORY_IDS
