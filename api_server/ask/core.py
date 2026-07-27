"""Async core for the ask feature: retrieve, synthesize, and stream.

The async functions here do the real LLM work. The streaming ``/ask`` route
awaits these directly; there is no synchronous registry wrapper - ``ask`` is an
api_server-only feature.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

from loguru import logger as log

from api_server.ask.retriever import DocChunk, get_retriever
from api_server.ask.signatures import AnswerFromDocs
from common import global_config
from models.ask import AskInput, AskResult, AskResultItem
from utils.llm.dspy_inference import DSPYInference

_SCHEMA_CONTEXT = "https://schema.org"


def _resolve_query_id(inp: AskInput) -> str:
    return inp.query_id or uuid4().hex


def _resolve_site(inp: AskInput) -> str:
    return inp.site or global_config.ask.docs_base_url


def _chunk_schema_object(chunk: DocChunk) -> dict:
    """Build a Schema.org TechArticle for a retrieved chunk."""
    return {
        "@type": "TechArticle",
        "@context": _SCHEMA_CONTEXT,
        "name": chunk.heading,
        "url": chunk.url,
        "description": chunk.text[:200],
        "text": chunk.text,
    }


def _chunk_to_item(chunk: DocChunk, site: str) -> AskResultItem:
    return AskResultItem(
        url=chunk.url,
        name=chunk.heading,
        site=site,
        score=chunk.score,
        description=chunk.text[:200],
        schema_object=_chunk_schema_object(chunk),
    )


def _build_context(chunks: list[DocChunk]) -> str:
    blocks = [f"## {c.heading}\n{c.text}" for c in chunks]
    return "\n\n".join(blocks)


async def _generate_answer(question: str, context: str) -> str:
    if not context:
        return "The documentation does not contain information about this question."
    inference = DSPYInference(pred_signature=AnswerFromDocs, observe=True)
    result = await inference.run(question=question, context=context)
    return str(result.answer)


async def answer_question(inp: AskInput) -> AskResult:
    """Retrieve supporting chunks and synthesize a grounded answer."""
    query_id = _resolve_query_id(inp)
    site = _resolve_site(inp)
    chunks = get_retriever().search(inp.query, global_config.ask.top_k)
    items = [_chunk_to_item(c, site) for c in chunks]
    answer = await _generate_answer(inp.query, _build_context(chunks))
    return AskResult(query_id=query_id, answer=answer, results=items)


def _start_event(query_id: str) -> dict:
    return {
        "message_type": "start",
        "_meta": {
            "response_type": "nlws",
            "version": "0.1",
            "query_id": query_id,
        },
    }


def _result_event(index: int, schema_object: dict) -> dict:
    return {"message_type": "result", "index": index, "item": schema_object}


def _summary_item(answer: str) -> dict:
    return {
        "@type": "SearchSummary",
        "@context": _SCHEMA_CONTEXT,
        "text": answer,
    }


def _complete_event(query_id: str, error: str | None = None) -> dict:
    meta: dict = {
        "response_type": "nlws",
        "version": "0.1",
        "session_context": {"conversation_id": query_id},
    }
    if error is not None:
        meta["error"] = error
    return {"message_type": "complete", "_meta": meta}


async def stream_events(inp: AskInput) -> AsyncIterator[dict]:
    """Yield NLWeb SSE messages progressively: start, result(s), [summary], complete.

    ``start`` and the retrieved ``result`` events are emitted before the (slow)
    answer generation, so clients receive bytes immediately instead of waiting
    for the full LLM round-trip. ``mode="list"`` skips generation entirely.
    """
    query_id = _resolve_query_id(inp)
    site = _resolve_site(inp)
    yield _start_event(query_id)
    index = 0
    error: str | None = None
    try:
        chunks = get_retriever().search(inp.query, global_config.ask.top_k)
        for chunk in chunks:
            yield _result_event(index, _chunk_to_item(chunk, site).schema_object)
            index += 1
        if inp.mode != "list":
            answer = await _generate_answer(inp.query, _build_context(chunks))
            yield _result_event(index, _summary_item(answer))
    except Exception as exc:  # noqa: BLE001
        # Defensive boundary: an SSE generator must always close with a
        # `complete` event. Surface failures (missing LLM key, provider
        # errors, etc.) as a generic error in the terminal event - the real
        # cause is logged server-side, not leaked to the client - instead of
        # abruptly terminating the stream.
        log.warning("ask stream failed (query_id={}): {}", query_id, exc)
        error = "answer generation failed"
    yield _complete_event(query_id, error=error)
