"""Per-request ``X-PAYMENT`` header for the /mcp transport.

MCP tool calls have no ``Request`` object, so the streamable-HTTP middleware
lifts the ``X-PAYMENT`` header off the ASGI scope and binds it here, mirroring
how the authenticated user is bound in ``src/utils/current_user.py``. The MCP
tool wrapper reads it back when enforcing the paywall. Stays ``None`` for the
CLI and stdio transports, which are never paywalled.
"""

from contextvars import ContextVar, Token

_payment_header: ContextVar[str | None] = ContextVar("x_payment_header", default=None)


def current_payment_header() -> str | None:
    """Return the ``X-PAYMENT`` header for this request, if any."""
    return _payment_header.get()


def set_payment_header(value: str | None) -> "Token[str | None]":
    """Bind the ``X-PAYMENT`` header. Returns a token for :func:`reset_payment_header`."""
    return _payment_header.set(value)


def reset_payment_header(token: "Token[str | None]") -> None:
    _payment_header.reset(token)
