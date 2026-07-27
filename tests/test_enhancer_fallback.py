"""Crash-fallback behavior of enhanced MCP tools (`mcp_server._tool_factory`).

The fallback must never execute a service twice, and must never silently
re-execute a mutating service (duplicate Gmail draft / double charge).
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from mcp_server._tool_factory import make_tool
from mcp_server.enhancers import _enhancers, enhance
from services import _registry, get_registry, service
from tests.test_enhancers import _make_mock_ctx
from tests.test_template import TestTemplate


async def _crash_after_call(tool):
    """Enhancer that runs the service, emits partial output, then crashes."""
    tool.call()
    tool.send_text("DO NOT SHIP THIS")
    raise RuntimeError("crash after service ran")


async def _crash_before_call(tool):
    """Enhancer that crashes before ever invoking the service."""
    raise RuntimeError("crash before service")


async def _call_twice(tool):
    """Enhancer that invokes the service twice (call, then call again)."""
    tool.call()
    return tool.call()


class TestEnhancerCrashFallback(TestTemplate):
    """Verify @enhance(fallback=...) handling when the enhancer raises."""

    def _register_test_service_and_get_tool_fn(
        self, fallback_mode, enhancer_fn, *, mutating=False, fail_on_call=None
    ):
        """Register a throwaway service + enhancer, register the tool.

        Returns (tool_fn, calls, cleanup); `calls["count"]` tracks how many
        times the pure service actually executed. `fail_on_call=N` makes the
        service raise on its Nth invocation."""

        class _CrashIn(BaseModel):
            x: int = 0

        class _CrashOut(BaseModel):
            value: int

        svc_name = f"__crash_test_{fallback_mode}"
        calls = {"count": 0}

        @service(
            name=svc_name,
            description="test",
            input_model=_CrashIn,
            output_model=_CrashOut,
            mutating=mutating,
        )
        def _svc(input: _CrashIn) -> _CrashOut:
            calls["count"] += 1
            if calls["count"] == fail_on_call:
                raise ValueError(f"service failed on call {fail_on_call}")
            return _CrashOut(value=input.x * 100)

        enhance(svc_name, fallback=fallback_mode)(enhancer_fn)

        entry = next(e for e in get_registry() if e.name == svc_name)
        test_mcp = FastMCP("test_crash")
        make_tool(test_mcp, entry)
        tool_fn = test_mcp._tool_manager._tools[svc_name].fn

        def cleanup():
            _registry[:] = [e for e in _registry if e.name != svc_name]
            _enhancers.pop(svc_name, None)

        return tool_fn, calls, cleanup

    def test_crash_with_headless_fallback_returns_pure_service_result(self):
        async def crashing_enhancer(tool):
            raise RuntimeError("simulated enhancer failure")

        tool_fn, _calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", crashing_enhancer
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=7))
            # Result is a CallToolResult with the pure service's output
            assert result.structuredContent == {"value": 700}
        finally:
            cleanup()

    def test_crash_with_error_fallback_propagates(self):
        async def crashing_enhancer(tool):
            raise RuntimeError("boom")

        tool_fn, _calls, cleanup = self._register_test_service_and_get_tool_fn(
            "error", crashing_enhancer
        )
        try:
            ctx = _make_mock_ctx()
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(tool_fn(ctx=ctx, x=1))
        finally:
            cleanup()

    def test_partial_output_discarded_on_crash(self):
        """If the enhancer attaches content/app meta and *then* crashes, the
        fallback CallToolResult must not ship that partial output."""

        async def crashing_after_partial(tool):
            tool.send_text("DO NOT SHIP THIS")
            tool.send_app("ui://should-be-discarded")
            raise RuntimeError("crash after partial")

        tool_fn, _calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", crashing_after_partial
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=3))
            assert result.structuredContent == {"value": 300}
            # Only the auto-generated text block; no DO NOT SHIP THIS.
            assert all(
                "DO NOT SHIP" not in c.text for c in result.content if c.type == "text"
            )
            assert result.meta is None
        finally:
            cleanup()

    def test_crash_after_call_reuses_result_and_runs_service_once(self):
        """If the enhancer crashes *after* tool.call() completed, the fallback
        must reuse the stashed result - never execute the service twice."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", _crash_after_call, mutating=True
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=4))
            assert result.structuredContent == {"value": 400}
            assert calls["count"] == 1
            # Partial enhancer output from before the crash is still discarded.
            assert all(
                "DO NOT SHIP" not in c.text for c in result.content if c.type == "text"
            )
        finally:
            cleanup()

    def test_crash_after_call_on_non_mutating_service_reuses_stash(self):
        """Non-mutating too: a completed call is reused, not re-executed."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", _crash_after_call
        )
        try:
            result = asyncio.run(tool_fn(ctx=_make_mock_ctx(), x=6))
            assert result.structuredContent == {"value": 600}
            assert calls["count"] == 1
        finally:
            cleanup()

    def test_crash_before_call_on_mutating_service_propagates(self):
        """No completed result + mutating service: re-execution is forbidden,
        so the enhancer error must propagate and the service must never run."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", _crash_before_call, mutating=True
        )
        try:
            ctx = _make_mock_ctx()
            with pytest.raises(RuntimeError, match="crash before service"):
                asyncio.run(tool_fn(ctx=ctx, x=4))
            assert calls["count"] == 0
        finally:
            cleanup()

    def test_crash_before_call_on_non_mutating_service_retries_headless(self):
        """Non-mutating services keep the headless retry: service runs once."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", _crash_before_call
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=5))
            assert result.structuredContent == {"value": 500}
            assert calls["count"] == 1
        finally:
            cleanup()

    def test_error_fallback_ignores_stashed_result(self):
        """fallback="error": a completed tool.call() must never convert the
        enhancer crash into a success response."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "error", _crash_after_call
        )
        try:
            with pytest.raises(RuntimeError, match="crash after service ran"):
                asyncio.run(tool_fn(ctx=_make_mock_ctx(), x=1))
            assert calls["count"] == 1
        finally:
            cleanup()

    def test_failed_second_call_on_mutating_service_propagates(self):
        """A stale stash from a completed first call() must not mask a failed
        second call() as success - the stash resets on every invocation."""
        tool_fn, calls, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", _call_twice, mutating=True, fail_on_call=2
        )
        try:
            with pytest.raises(ValueError, match="service failed on call 2"):
                asyncio.run(tool_fn(ctx=_make_mock_ctx(), x=2))
            assert calls["count"] == 2
        finally:
            cleanup()
