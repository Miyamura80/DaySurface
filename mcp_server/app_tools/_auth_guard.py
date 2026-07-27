"""Shared auth guard for app-only MCP tools.

App-only tools (``visibility=["app"]``) are registered directly on FastMCP and
skip the ``_make_headless_tool`` factory in ``mcp_server/_tool_factory.py`` -
which means they would otherwise bypass scope + quota checks AND trust an
attacker-supplied ``user_id`` field on the wire.

``guard_user_id`` re-runs the same scope + quota helpers the factory uses and
returns the user_id that the caller is allowed to act on:

- When an ``AuthenticatedUser`` is bound (HTTP / mcp-over-http), the
  authenticated principal's ``user_id`` is returned, ignoring whatever the
  iframe sent. This prevents cross-user mailbox access via tampered payloads.
- When no user is bound (CLI / stdio), the iframe-supplied value is trusted
  - it's the only signal we have, and these transports are single-tenant.
"""

from __future__ import annotations

from src.utils.current_user import current_user


def guard_user_id(claimed_user_id: str) -> str:
    """Enforce scopes + quota, return the user_id the caller may act on."""
    # Deliberate local import: _check_scopes/_check_quota reach api_server.*
    # at call time, and api_server.server circularly imports mcp_server - keep
    # the registration machinery out of app_tools module-import.
    from mcp_server._tool_factory import _check_quota, _check_scopes  # noqa: PLC0415

    _check_scopes()
    user = current_user()
    if user is not None:
        # HTTP / mcp-over-http: ignore the wire value, use the verified principal.
        # Burn quota only after we've decided which user to charge.
        _check_quota()
        return user.user_id
    # CLI / stdio: no auth context; trust the input.
    _check_quota()
    return claimed_user_id
