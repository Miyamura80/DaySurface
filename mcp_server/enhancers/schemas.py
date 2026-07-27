"""Pydantic models for elicitation forms.

Per MCP spec, elicitation schemas must be flat objects with primitive fields
(str, int, float, bool, Literal[...] enums). The Python SDK accepts list[str]
but the spec doesn't - avoid for cross-client compatibility.

Add elicitation schemas here as enhancers require them.
"""
