"""Public waitlist signup endpoint (no auth).

``POST /waitlist/join`` accepts ``{ email, company (honeypot), source }`` from
the static landing page, cross-origin. Auth in this app is per-route (a
``Depends(get_authenticated_user)`` on the routes that need it), so simply
omitting that dependency makes this route public - exactly like ``/health``.

Spam / abuse control is server-side, so it holds even with client JS off:
- a request-body size cap read incrementally, *before* parsing, so an oversized
  payload is aborted mid-stream instead of being pulled into memory (the
  honeypot field is intentionally unbounded at the model level, so the cap - not
  a field limit - is what bounds it);
- honeypot rejection (any non-empty ``company`` is dropped with success);
- email validation and unique-email dedupe (idempotent);
- the app-wide per-IP rate-limit middleware.

Every accepted request returns ``{ success: true }`` whether the row was new,
already present, or a bot tripped the honeypot, so the outcome never leaks. A
malformed body is 422; an oversized body is 413. The Resend audience sync and
notifications run in the background, off the request path.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from models.waitlist import WaitlistJoinInput, WaitlistJoinResult
from services import waitlist_svc

router = APIRouter(tags=["waitlist"])

# A signup body is tiny (an email plus a couple of short fields), so cap it well
# under any legitimate size. Read incrementally and abort once exceeded.
_MAX_BODY_BYTES = 8 * 1024


async def _read_capped(request: Request) -> bytes:
    """Read the request body, aborting with 413 once it exceeds the cap.

    Streams so an oversized (or chunked, Content-Length-less) payload never gets
    fully buffered in memory before we notice.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/waitlist/join",
    response_model=WaitlistJoinResult,
    summary="Join the waitlist (public)",
)
async def join_waitlist(
    request: Request, background: BackgroundTasks
) -> WaitlistJoinResult:
    body = await _read_capped(request)
    try:
        payload = WaitlistJoinInput.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid request.") from exc

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
    # record_signup is a blocking DB call; this handler is async, so run it in
    # the threadpool rather than on the event loop where it would stall other
    # requests under DB latency.
    created = await run_in_threadpool(waitlist_svc.record_signup, email, source)

    # Best-effort side effects, off the request path, and only for a genuinely
    # new signup so a repeat submit never re-notifies or re-emails.
    if created:
        background.add_task(waitlist_svc.sync_to_resend, email)
        background.add_task(waitlist_svc.notify_new_signup, email)
        background.add_task(waitlist_svc.send_confirmation, email)

    return WaitlistJoinResult(success=True)
