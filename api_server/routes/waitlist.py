"""Public waitlist signup endpoint (no auth).

``POST /waitlist/join`` accepts ``{ email, company (honeypot), source }`` from
the static landing page, cross-origin. Auth in this app is per-route (a
``Depends(get_authenticated_user)`` on the routes that need it), so simply
omitting that dependency makes this route public - exactly like ``/health``.

Spam control is server-side, so it holds even with client JS off: honeypot
rejection, email validation, unique-email dedupe (idempotent), and the app-wide
per-IP rate-limit middleware. Every accepted request returns ``{ success: true }``
whether the row was new, already present, or a bot tripped the honeypot, so the
outcome never leaks to a caller. The only non-200 is a genuine bad email (422),
which the form surfaces so a real user can correct it.

The Resend audience sync and notifications run in the background, off the
request path, so a slow third party never delays the response.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from models.waitlist import WaitlistJoinInput, WaitlistJoinResult
from services import waitlist_svc

router = APIRouter(tags=["waitlist"])


@router.post(
    "/waitlist/join",
    response_model=WaitlistJoinResult,
    summary="Join the waitlist (public)",
)
def join_waitlist(
    payload: WaitlistJoinInput, background: BackgroundTasks
) -> WaitlistJoinResult:
    # Honeypot: a filled hidden field means a bot. Report success without
    # storing so it cannot tell it was caught.
    if payload.company.strip():
        return WaitlistJoinResult(success=True)

    email = payload.email.strip().lower()
    if not waitlist_svc.is_valid_email(email):
        raise HTTPException(
            status_code=422, detail="A valid email address is required."
        )

    source = (payload.source or "waitlist-page")[:64]
    created = waitlist_svc.record_signup(email, source)

    # Best-effort side effects, off the request path, and only for a genuinely
    # new signup so a repeat submit never re-notifies or re-emails.
    if created:
        background.add_task(waitlist_svc.sync_to_resend, email)
        background.add_task(waitlist_svc.notify_new_signup, email)
        background.add_task(waitlist_svc.send_confirmation, email)

    return WaitlistJoinResult(success=True)
