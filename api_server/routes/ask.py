"""NLWeb-conformant ``/ask`` natural-language Q&A endpoint.

Public + IP-rate-limited (no auth). This is an api_server-only feature: the
``/ask`` route is the ONLY transport. ``ask`` is not a registry service, CLI
command, or MCP tool, so it can never appear on ``/mcp``.
Ships inert: returns 404 when ``ask.enabled`` is False. Supports SSE streaming
(``streaming=true``, the NLWeb default) and a non-streaming JSON fallback.

This route is exempt from ``RateLimitMiddleware`` (see middleware/rate_limit.py)
so the SSE stream is not buffered through BaseHTTPMiddleware; IP rate limiting is
enforced inline here instead.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from limits import RateLimitItemPerMinute
from limits.strategies import MovingWindowRateLimiter
from starlette.responses import JSONResponse, StreamingResponse

from api_server.ask.core import answer_question, stream_events
from api_server.middleware.rate_limit import _build_storage, _client_ip
from common import global_config
from models.ask import AskInput, AskResult

router = APIRouter(tags=["ask"])

# Reuse the main middleware's storage selection (Redis when configured) so /ask
# limits hold across workers/replicas, and its trusted-proxy _client_ip so
# proxied deployments resolve the real client IP rather than the proxy's.
_limiter = MovingWindowRateLimiter(_build_storage())


def _split_prev(prev: str | None) -> list[str]:
    """Parse the NLWeb comma-separated ``prev`` query field into a list."""
    if not prev:
        return []
    return [p.strip() for p in prev.split(",") if p.strip()]


def _enforce_rate_limit(request: Request) -> None:
    """IP-based rate limit; raises 429 when exceeded."""
    per_minute = global_config.ask.rate_limit_per_minute
    item = RateLimitItemPerMinute(per_minute)
    identity = "ask_ip:" + _client_ip(request)
    if not _limiter.hit(item, identity):
        stats = _limiter.get_window_stats(item, identity)
        retry_after = max(1, int(stats.reset_time - time.time()))
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Retry shortly.",
            headers={"Retry-After": str(retry_after)},
        )


def _guard_enabled() -> None:
    if not global_config.ask.enabled:
        # Behave as if the endpoint does not exist when disabled.
        raise HTTPException(status_code=404)


def _to_json_payload(result: AskResult) -> dict:
    """Map AskResult to the documented non-streaming NLWeb JSON shape.

    The generated answer is included as a SearchSummary entry at index 0.
    """
    summary = {
        "url": "",
        "name": "Answer",
        "site": global_config.ask.docs_base_url,
        "score": 1.0,
        "description": result.answer,
        "schema_object": {
            "@type": "SearchSummary",
            "@context": "https://schema.org",
            "text": result.answer,
        },
    }
    results = [summary] + [item.model_dump() for item in result.results]
    return {"query_id": result.query_id, "results": results}


async def _sse(inp: AskInput) -> AsyncIterator[str]:
    async for event in stream_events(inp):
        yield f"data: {json.dumps(event)}\n\n"


async def _handle(request: Request, inp: AskInput) -> object:
    _guard_enabled()
    _enforce_rate_limit(request)
    if inp.streaming:
        return StreamingResponse(
            _sse(inp),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    result = await answer_question(inp)
    return JSONResponse(content=_to_json_payload(result))


@router.post("/ask")
async def ask_post(request: Request, body: AskInput) -> object:
    return await _handle(request, body)


@router.get("/ask")
async def ask_get(
    request: Request,
    query: str,
    streaming: bool = True,
    mode: Literal["list", "summarize", "generate"] = "generate",
    query_id: str | None = None,
    prev: str | None = None,
    site: str | None = None,
    decontextualized_query: str | None = None,
) -> object:
    inp = AskInput(
        query=query,
        streaming=streaming,
        mode=mode,
        query_id=query_id,
        prev=_split_prev(prev),
        site=site,
        decontextualized_query=decontextualized_query,
    )
    return await _handle(request, inp)
