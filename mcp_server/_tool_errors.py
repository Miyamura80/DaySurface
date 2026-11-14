"""Which tool exceptions the model is allowed to read.

python-sdk 2.x sanitizes tool failures: only a ``ToolError`` keeps its text;
any other exception reaches the client as a bare ``Error executing tool
<name>`` (``UnexpectedToolError``, original kept as ``__cause__``). v1 passed
every exception's text through.

This template's services are written to fail with model-facing messages - a
``ConnectRequiredError`` carries its own recovery script, PDF/Gmail domain
errors say what to do next, the scope check and the daily quota explain the
refusal - and the pure services must not import MCP types to say so. So the
server (``mcp_server.server._DaySurfaceMCPServer``) re-labels the crash as a
``ToolError`` when its cause is one of those *first-party* failures, and keeps
the SDK's sanitization for everything else (SQLAlchemy, HTTP client, and other
third-party crashes whose text may carry internals).
"""

from starlette.exceptions import HTTPException

# Exceptions defined in these packages are written for the caller to read.
_FIRST_PARTY_PREFIXES = ("services", "models", "api_server", "src", "common")

# Builtins the tool wrappers raise deliberately: PermissionError from the
# scope check, ValueError (incl. pydantic ValidationError) from input models
# and services rejecting arguments - the model's mistake to read and correct.
_MODEL_FACING_BUILTINS: tuple[type[BaseException], ...] = (
    PermissionError,
    ValueError,
)


def model_facing_message(exc: BaseException | None) -> str | None:
    """Return the text the model may see for ``exc``, or None to stay sanitized."""
    if exc is None:
        return None
    if isinstance(exc, HTTPException):
        # The daily quota (429) and paywall refusals are HTTP-shaped.
        return str(exc.detail)
    if isinstance(exc, _MODEL_FACING_BUILTINS):
        return str(exc)
    module = type(exc).__module__
    if module.split(".", 1)[0] in _FIRST_PARTY_PREFIXES:
        return str(exc)
    return None
