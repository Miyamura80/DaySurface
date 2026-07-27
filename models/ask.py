"""Models for the ask (NLWeb Q&A) service.

Pure Pydantic shapes shared by the CLI, HTTP /ask route, and the registry
service wrapper. Field shapes mirror the NLWeb REST API request params and
Schema.org result envelope (see docs/content/docs/api/ask.mdx).
"""

from typing import Literal

from pydantic import BaseModel, Field


class AskInput(BaseModel):
    """A natural-language question plus NLWeb conversation context."""

    query: str
    streaming: bool = True
    mode: Literal["list", "summarize", "generate"] = "generate"
    query_id: str | None = None
    prev: list[str] = Field(default_factory=list)
    site: str | None = None
    decontextualized_query: str | None = None


class AskResultItem(BaseModel):
    """A single retrieved doc chunk, mapped to the NLWeb result shape."""

    url: str
    name: str
    site: str
    score: float
    description: str
    schema_object: dict


class AskResult(BaseModel):
    """The full answer: generated text plus the supporting result items."""

    query_id: str
    answer: str
    results: list[AskResultItem]
