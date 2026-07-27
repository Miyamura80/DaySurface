"""Per-request authenticated user for transports that authenticate (HTTP, /mcp).

Stays ``None`` for the CLI and stdio MCP transport. Services that need a user
should treat ``None`` as "unauthenticated / local" rather than raising -- that
keeps them pure and transport-agnostic.
"""

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api_server.auth import AuthenticatedUser

_current_user: ContextVar["AuthenticatedUser | None"] = ContextVar(
    "current_user", default=None
)


def current_user() -> "AuthenticatedUser | None":
    """Return the authenticated user for this request, if any."""
    return _current_user.get()


def set_current_user(
    user: "AuthenticatedUser | None",
) -> "Token[AuthenticatedUser | None]":
    """Set the authenticated user. Returns a token for :func:`reset_current_user`."""
    return _current_user.set(user)


def reset_current_user(token: "Token[AuthenticatedUser | None]") -> None:
    _current_user.reset(token)
