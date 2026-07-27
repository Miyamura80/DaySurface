"""Internal tool-registration factory for `mcp_server.server`.

Builds FastMCP tools from `ServiceEntry` records, branching between two paths:

- **Headless**: sync wrapper, returns the Pydantic output model. FastMCP derives
  `outputSchema` from the return annotation.
- **Enhanced**: async wrapper with `Context`, calls an `@enhance`-registered
  function. Returns a `CallToolResult`. We patch `outputSchema` explicitly
  because FastMCP doesn't derive it when a tool returns `CallToolResult`.

Don't reach for these helpers from feature code - use `@service` and `@enhance`.
"""

import inspect
from typing import Any

from loguru import logger as log
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from mcp_server.enhancers import EnhancerEntry, get_enhancer
from mcp_server.enhancers.base import EnhancedTool, build_app_meta
from mcp_server.url_elicitation import reraise_with_elicitation
from services import ConnectRequiredError, ServiceEntry
from src.utils.current_user import current_user


def _check_scopes() -> None:
    """Enforce ``services:execute`` scope for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    """
    user = current_user()
    if user is None:
        return

    # Circular import: api_server.server mounts mcp_server.server, which
    # imports this module - api_server.* must only load at call time.
    from api_server.auth.scopes import SERVICES_EXECUTE, check_scopes  # noqa: PLC0415

    if not check_scopes([SERVICES_EXECUTE], user.scopes):
        raise PermissionError(
            "Insufficient permissions: 'services:execute' scope required."
        )


def _check_quota() -> None:
    """Enforce daily quota for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    Called *after* input validation so malformed requests don't burn quota.
    """
    user = current_user()
    if user is None:
        return

    # Circular import: api_server.server mounts mcp_server.server, which
    # imports this module - api_server.* must only load at call time.
    from api_server.billing.limits import ensure_daily_limit  # noqa: PLC0415

    ensure_daily_limit(user.user_id)


def _current_session(mcp: FastMCP) -> ServerSession | None:
    """Return the live ServerSession, or None outside an MCP request.

    Direct in-process invocations (unit tests calling a tool function) have no
    request context; None makes capability support read as "unknown".
    """
    try:
        return mcp.get_context().session
    except (LookupError, ValueError):
        return None


def make_tool(mcp: FastMCP, entry: ServiceEntry) -> None:
    """Register a service as an MCP tool - enhanced if an enhancer exists, else headless."""
    enhancer_entry = get_enhancer(entry.name)
    if enhancer_entry is not None:
        _make_enhanced_tool(mcp, entry, enhancer_entry)
    else:
        _make_headless_tool(mcp, entry)


def _make_headless_tool(mcp: FastMCP, entry: ServiceEntry) -> None:
    """Sync wrapper. Returns the Pydantic output model so FastMCP derives outputSchema."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    def tool_fn(**kwargs):
        _check_scopes()
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            user = current_user()
            if user is not None:
                kwargs["user_id"] = user.user_id
            elif not kwargs.get("user_id"):
                kwargs.setdefault("user_id", "")
        input_obj = input_model(**kwargs)
        _check_quota()
        try:
            return func(input_obj)
        except ConnectRequiredError as exc:
            # MCP-only affordance: upgrade to the SEP-1036 URL-elicitation
            # error (-32042) so capable hosts open the consent flow natively.
            reraise_with_elicitation(_current_session(mcp), exc)

    _apply_tool_signature(tool_fn, entry, return_annotation=output_model)
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)


def _make_enhanced_tool(
    mcp: FastMCP, entry: ServiceEntry, enhancer_entry: EnhancerEntry
) -> None:
    """Async wrapper that calls the enhancer with an `EnhancedTool`."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    async def tool_fn(ctx: Context, **kwargs) -> CallToolResult:
        _check_scopes()
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            user = current_user()
            if user is not None:
                kwargs["user_id"] = user.user_id
            elif not kwargs.get("user_id"):
                kwargs.setdefault("user_id", "")
        input_obj = input_model(**kwargs)
        _check_quota()
        tool = EnhancedTool(ctx=ctx, input=input_obj, service_fn=func)
        try:
            result = await enhancer_entry.fn(tool)
        except ConnectRequiredError as exc:
            # Expected condition, not an enhancer crash: the headless fallback
            # would only raise it again. MCP-only affordance: upgrade to the
            # SEP-1036 URL-elicitation error (-32042) when possible.
            reraise_with_elicitation(ctx.session, exc)
        except Exception as enhancer_exc:  # noqa: BLE001
            # Enhancer failures of any kind must fall back to the pure service
            # so MCP clients still get a structured result on the headless path.
            if enhancer_entry.fallback == "error":
                raise
            result = _recover_from_enhancer_crash(tool, entry, ctx, enhancer_exc)
            # Discard any partial output the enhancer accumulated before crashing.
            tool.extra_content = []
            tool.app_resource_uri = None

        return _build_call_tool_result(result, tool)

    _apply_tool_signature(
        tool_fn, entry, return_annotation=CallToolResult, include_context=True
    )
    # Declare the app's ui:// resource on the tool definition so hosts see it
    # in tools/list (per MCP Apps spec) and not only on the call result.
    meta = (
        build_app_meta(enhancer_entry.app_uri)
        if enhancer_entry.app_uri is not None
        else None
    )
    mcp.tool(name=entry.name, description=entry.description, meta=meta)(tool_fn)
    _patch_output_schema(mcp, entry.name, output_model)


def _recover_from_enhancer_crash(
    tool: EnhancedTool,
    entry: ServiceEntry,
    ctx: Context,
    enhancer_exc: Exception,
) -> BaseModel:
    """Resolve the headless-fallback result after an enhancer crash.

    Never executes the service twice:

    - If the enhancer's last ``tool.call()`` completed, reuse that stashed
      result - re-invoking the service would duplicate side effects for
      mutating services (e.g. a second Gmail draft or a double charge).
    - If there is no completed result and the service is ``mutating``,
      propagate the enhancer error instead of silently re-executing.
    - Otherwise (non-mutating, never completed) retry headless via
      ``tool.call()`` - the single code path that invokes the service.
    """
    if tool.call_result is not None:
        log.opt(exception=enhancer_exc).error(
            "enhancer for {!r} crashed after tool.call(); reusing the "
            "completed service result",
            entry.name,
        )
        return tool.call_result
    if entry.mutating:
        raise enhancer_exc
    log.opt(exception=enhancer_exc).error(
        "enhancer for {!r} crashed; falling back to headless", entry.name
    )
    try:
        return tool.call()
    except ConnectRequiredError as exc:
        reraise_with_elicitation(ctx.session, exc)


def _patch_output_schema(mcp: FastMCP, tool_name: str, output_model: type) -> None:
    """Publish outputSchema for tools that return CallToolResult.

    FastMCP only derives outputSchema from a tool's return type annotation,
    and our enhanced wrappers return CallToolResult so the structured output
    is opaque to it. We patch the registered Tool's output_schema directly.

    KNOWN FRAGILITY: this reaches into `mcp._tool_manager._tools` (double-
    private). FastMCP has no public API for setting outputSchema on a tool
    that returns CallToolResult. Backstopped by integration tests in
    `tests/test_mcp_server.py::test_enhanced_tools_publish_output_schema`.
    """
    if not issubclass(output_model, BaseModel):
        return
    try:
        tools_registry = mcp._tool_manager._tools
    except AttributeError:
        log.warning(
            "FastMCP private _tool_manager._tools not found; outputSchema for "
            "{!r} will not be published. Likely an SDK upgrade incompatibility.",
            tool_name,
        )
        return
    tool = tools_registry.get(tool_name)
    if tool is None:
        log.warning(
            "Tool {!r} not found in FastMCP registry; outputSchema not published.",
            tool_name,
        )
        return
    tool.__dict__["output_schema"] = output_model.model_json_schema()


def _resolve_signature_default(param: inspect.Parameter, field_info: Any) -> Any:
    """Return a signature-safe default for a synthesized tool parameter.

    ``inspect.signature(model)`` renders a Pydantic ``default_factory`` field
    with the private ``<factory>`` sentinel instead of a real value. FastMCP's
    ``func_metadata`` copies each parameter's ``default`` verbatim into the
    tool's argument model (see its ``func_arg_to_pydantic_field``), so that
    sentinel would be handed back as the argument value whenever the caller
    omits the field - failing validation with "Input should be a valid list
    ... input_value=<factory>" and making a nominally optional field
    effectively required. Resolve the factory to a concrete value so the field
    stays genuinely optional.

    ``FieldInfo.get_default`` runs the factory for us, dispatching between the
    zero-arg and validated-data factory forms by arity (``validated_data`` is
    ignored by a zero-arg factory) and returning a fresh value per call - so
    mutable defaults stay isolated across tool invocations.
    """
    if field_info is None or field_info.default_factory is None:
        return param.default
    return field_info.get_default(call_default_factory=True, validated_data={})


def _apply_tool_signature(
    tool_fn: Any,
    entry: ServiceEntry,
    return_annotation: type,
    include_context: bool = False,
) -> None:
    """Synthesize __signature__ and __annotations__ so FastMCP derives input/output schema."""
    tool_fn.__name__ = entry.name
    tool_fn.__doc__ = entry.description

    input_sig = inspect.signature(entry.input_model)
    annotations: dict = {k: v.annotation for k, v in input_sig.parameters.items()}
    if include_context:
        annotations["ctx"] = Context
    annotations["return"] = return_annotation
    tool_fn.__annotations__ = annotations

    # Map each signature parameter to its FieldInfo (keyed by alias when set,
    # matching how Pydantic names the synthesized __init__ parameters) so we can
    # resolve default_factory sentinels below.
    field_by_param = {
        (fi.alias or name): fi
        for name, fi in entry.input_model.model_fields.items()  # ty: ignore[unresolved-attribute]
    }
    params = [
        p.replace(default=_resolve_signature_default(p, field_by_param.get(name)))
        for name, p in input_sig.parameters.items()
    ]
    if include_context:
        ctx_param = inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context
        )
        params = [ctx_param, *params]
    tool_fn.__signature__ = input_sig.replace(
        parameters=params, return_annotation=return_annotation
    )


def _build_call_tool_result(result: BaseModel, tool: EnhancedTool) -> CallToolResult:
    """Assemble CallToolResult from a service result + accumulated extras.

    `result` must be a Pydantic BaseModel - the service's output_model
    instance. Other return types are rejected so outputSchema/structuredContent
    stay consistent and failures surface here rather than as opaque
    pydantic_core ValidationErrors deep inside CallToolResult.
    """
    if not isinstance(result, BaseModel):
        raise TypeError(
            f"Enhanced tool result must be a Pydantic BaseModel, got "
            f"{type(result).__name__}. Enhancers must return their service's "
            "output_model instance."
        )
    content: list = [
        TextContent(type="text", text=result.model_dump_json()),
        *tool.extra_content,
    ]
    kwargs: dict = {"content": content, "structuredContent": result.model_dump()}
    app_meta = tool.app_meta()
    if app_meta is not None:
        kwargs["_meta"] = app_meta
    return CallToolResult(**kwargs)
