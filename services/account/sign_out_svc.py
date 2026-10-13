"""Sign out everywhere: end every WorkOS session the user holds.

DaySurface keeps no session of its own. Each request carries a WorkOS-issued
access token, and the long-lived state is the WorkOS session plus its refresh
token, so ending those sessions is what logging out means here. WorkOS treats a
revoked session's refresh token as terminal (``invalid_grant``), so every client
signed in under it must sign in again. Access tokens already issued are
stateless JWTs and stop working when they expire, within minutes.

Uses the WorkOS Sessions API: list the user's sessions, then revoke each active
one by id.
"""

from collections.abc import Callable
from urllib.parse import quote

import httpx
from loguru import logger as log

from common import global_config
from models.account import SignOutInput, SignOutResult
from services import ClientRefusalError, RetryLaterError, service

WORKOS_API_BASE = "https://api.workos.com"
_PAGE_LIMIT = 100  # WorkOS maximum
# Bounds a cursor that never terminates. 20 pages is 2,000 sessions, far beyond
# any real user.
_MAX_PAGES = 20
_TIMEOUT_SECONDS = 10.0
_RETRY_MESSAGE = (
    "WorkOS could not end the sessions right now. Try signing out again in a minute."
)


def _workos_client() -> httpx.Client:
    api_key = global_config.WORKOS_API_KEY
    if not api_key:
        raise RuntimeError("Sign-out is unavailable: WORKOS_API_KEY is not configured.")
    return httpx.Client(
        base_url=WORKOS_API_BASE,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_TIMEOUT_SECONDS,
    )


def _send(
    method: Callable[..., httpx.Response], path: str, *, revoked: int, **kwargs
) -> httpx.Response:
    """Call WorkOS; map rate limits and outages to ``RetryLaterError``.

    404 is returned to the caller, which decides whether it means "nothing to
    do". Any other 4xx raises: it is a misconfiguration, not something a retry
    fixes.
    """
    try:
        response = method(path, **kwargs)
    except httpx.TransportError as exc:
        raise RetryLaterError(
            _RETRY_MESSAGE, side_effects_possible=revoked > 0
        ) from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryLaterError(_RETRY_MESSAGE, side_effects_possible=revoked > 0)
    if response.status_code != 404:
        response.raise_for_status()
    return response


def _active_session_ids(client: httpx.Client, user_id: str) -> list[str]:
    path = f"/user_management/users/{quote(user_id, safe='')}/sessions"
    session_ids: list[str] = []
    after: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict[str, str | int] = {"limit": _PAGE_LIMIT}
        if after:
            params["after"] = after
        response = _send(client.get, path, revoked=0, params=params)
        if response.status_code == 404:
            # WorkOS has no such user, so there is no session to end.
            return session_ids
        page = response.json()
        session_ids.extend(
            entry["id"]
            for entry in page.get("data", [])
            # The endpoint lists active sessions; skip anything marked otherwise.
            if entry.get("status", "active") == "active"
        )
        after = (page.get("list_metadata") or {}).get("after")
        if not after:
            return session_ids
    raise RuntimeError("WorkOS returned more session pages than expected.")


@service(
    name="sign_out",
    description=(
        "Sign out of DaySurface everywhere: end all of the user's sessions so "
        "every connected client must sign in again. Does not disconnect Gmail "
        "(gmail_disconnect does that)."
    ),
    input_model=SignOutInput,
    output_model=SignOutResult,
    mutating=True,
)
def sign_out(input: SignOutInput) -> SignOutResult:
    if not input.user_id:
        raise ClientRefusalError("There is no signed-in user to sign out.")

    revoked = 0
    with _workos_client() as client:
        for session_id in _active_session_ids(client, input.user_id):
            response = _send(
                client.post,
                "/user_management/sessions/revoke",
                revoked=revoked,
                json={"session_id": session_id},
            )
            # 404: the session ended between listing and revoking, which is
            # already the outcome we want.
            if response.status_code != 404:
                revoked += 1

    log.info("sign_out ended {} WorkOS session(s)", revoked)
    return SignOutResult(sessions_revoked=revoked)
