"""Service registry - pure business logic with no transport awareness."""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ServiceEntry:
    name: str
    description: str
    input_model: type
    output_model: type
    func: Callable[..., Any]
    mutating: bool = False
    # Admin/dev-only services declare the scope required to reach them over a
    # networked transport (e.g. "admin:read" / "admin:write"). None means an
    # ordinary service callable with "services:execute". This is the single
    # source of truth for the admin boundary: the HTTP transport enforces it as
    # a required scope, and the MCP transport hides these tools from the LLM
    # surface. A new admin tool is gated everywhere the moment it sets this.
    admin_scope: str | None = None
    # Keep this service off the default LLM (MCP) tool surface while leaving it
    # callable on HTTP and via app-only tools. For operations the model must not
    # invoke autonomously from untrusted content (prompt-injection-sensitive
    # side effects) but which users still drive through a UI, and for
    # demo/noise services. Unlike admin_scope this does not change HTTP scope.
    hide_from_llm: bool = False


class ConnectRequiredError(Exception):
    """A service call requires the user to first complete an external connect flow.

    Transport-agnostic contract: each integration that links a third-party
    account (Gmail today; any future OAuth-backed service) raises its own
    subclass so transports can offer a recovery affordance without importing
    the feature - the MCP layer converts this into a SEP-1036 URL elicitation
    carrying ``build_auth_url()``. Two obligations on subclasses:

    - ``message`` must itself be a self-recovering script (what tool to call,
      what to do with the URL, then retry), because for hosts with no native
      affordance the exception text is the only channel that reaches them.
    - ``build_auth_url()`` returns the URL where the user completes the flow,
      or None when the flow is unconfigured in this deployment.
    """

    def __init__(self, user_id: str, message: str, *, elicitation_message: str) -> None:
        self.user_id = user_id
        self.elicitation_message = elicitation_message
        super().__init__(message)

    def build_auth_url(self) -> str | None:
        """Return the connect-flow URL for this user, or None if unconfigured."""
        raise NotImplementedError


_registry: list[ServiceEntry] = []
_discovered: bool = False


def service(
    *,
    name: str,
    description: str,
    input_model: type,
    output_model: type,
    mutating: bool = False,
    admin_scope: str | None = None,
    hide_from_llm: bool = False,
):
    """Decorator that registers a function as a service.

    Set ``mutating=True`` for services with side effects (create/charge/send)
    so the HTTP transport enforces ``Idempotency-Key`` and replays the stored
    response on retries. Leave it ``False`` (the default) for pure/read-only
    services. On MCP, the flag also gates the enhancer crash-fallback
    (``mcp_server/_tool_factory.py``): a mutating service is never silently
    re-executed - the fallback reuses the already-completed ``tool.call()``
    result or propagates the enhancer error. CLI behavior is unchanged.

    Set ``admin_scope`` (e.g. ``"admin:read"`` / ``"admin:write"``) for
    admin/dev-only services - config introspection/tampering, host diagnostics
    that shell out. It is the single source of truth for the admin boundary:
    the HTTP transport requires that scope on the auto-generated route (ordinary
    ``services:execute`` keys get 403), and the MCP transport hides the tool
    from the LLM surface. CLI callers are unaffected.

    Set ``hide_from_llm=True`` to keep a service off the default LLM (MCP) tool
    surface while leaving it callable on HTTP and via app-only tools - for
    mutating operations the model must not invoke autonomously from untrusted
    content (e.g. webhook subscription, a prompt-injection exfil risk) but which
    users drive through a UI, and for demo services. It does not change HTTP
    scope, unlike ``admin_scope``.
    """

    def decorator(func):
        _registry.append(
            ServiceEntry(
                name=name,
                description=description,
                input_model=input_model,
                output_model=output_model,
                func=func,
                mutating=mutating,
                admin_scope=admin_scope,
                hide_from_llm=hide_from_llm,
            )
        )
        return func

    return decorator


def discover_services() -> None:
    """Import every ``services.*`` submodule so @service decorators run.

    Idempotent: safe to call from multiple transports during startup.
    """
    global _discovered
    if _discovered:
        return
    for module_info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"services.{module_info.name}")  # noqa: TID251 - service auto-discovery so @service decorators register on startup
    _discovered = True


def get_registry() -> list[ServiceEntry]:
    """Return all registered services."""
    return list(_registry)
