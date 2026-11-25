"""Which tool exceptions the model is allowed to read.

python-sdk 2.x sanitizes tool failures: only a ``ToolError`` keeps its text;
any other exception reaches the client as a bare ``Error executing tool
<name>`` (``UnexpectedToolError``, original kept as ``__cause__``). v1 passed
every exception's text through.

Services are written to fail with model-facing messages - a
``ConnectRequiredError`` carries its own recovery script, PDF/Gmail domain
errors say what to do next, the daily quota explains the refusal - and the
pure services must not import MCP types to say so. They mark such failures by
raising a ``services.ModelFacingError`` subclass, whose contract is that the
text is composed first-party and never wraps third-party text. The server
(``mcp_server.server._DaySurfaceMCPServer``) re-labels a crash as a
``ToolError`` only when its cause is one of those; everything else stays
sanitized - including builtins such as ``ValueError``/``PermissionError`` and
exceptions merely *defined* in a first-party module, because their text is
routinely third-party (google-auth ``MalformedError`` is a ``ValueError``; a
filesystem ``PermissionError`` names a server path; ``ImageFetchError`` can
embed an HTTP-client error).

The MCP wrappers' own refusals (scope check, input-model validation) raise
``ToolError`` directly in ``mcp_server._tool_factory`` and never reach here.
"""

import json

from starlette.exceptions import HTTPException

from services import ModelFacingError

# Module whose HTTPExceptions are written for the caller: the daily quota (429)
# and paywall refusals. Matched on the raising frame, not the exception class,
# so an HTTPException raised by a dependency is not trusted by accident.
_BILLING_MODULE_PREFIX = "api_server.billing"


def _raised_by_billing(exc: BaseException) -> bool:
    """True when the innermost traceback frame is first-party billing code."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return False
    module = tb.tb_frame.f_globals.get("__name__", "")
    return module == _BILLING_MODULE_PREFIX or module.startswith(
        _BILLING_MODULE_PREFIX + "."
    )


def _http_detail_text(detail: object) -> str:
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
        return json.dumps(detail, default=str)
    return str(detail)


def model_facing_message(exc: BaseException | None) -> str | None:
    """Return the text the model may see for ``exc``, or None to stay sanitized."""
    if exc is None:
        return None
    if isinstance(exc, ModelFacingError):
        return str(exc)
    if isinstance(exc, HTTPException) and _raised_by_billing(exc):
        return _http_detail_text(exc.detail)
    return None
