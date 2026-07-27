"""Tests for the MCP enhancer infrastructure (EnhancedTool, registration, fallback)."""

import asyncio
import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import FastMCP
from mcp.types import AudioContent, TextContent
from pydantic import BaseModel

from mcp_server._tool_factory import _build_call_tool_result, make_tool
from mcp_server.enhancers import _enhancers, enhance, get_enhancer
from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.config import (
    _format_config_lines,
    _render_config_image,
    config_show_enhanced,
)
from models.config import ConfigShowInput, ConfigShowResult
from services import _registry, get_registry, service
from tests.test_template import TestTemplate


class _Input(BaseModel):
    n: int = 0


class _Output(BaseModel):
    doubled: int


def _service(input: _Input) -> _Output:
    return _Output(doubled=input.n * 2)


def _make_mock_ctx(*, can_elicit: bool = True, elicit_result: Any = None) -> MagicMock:
    ctx = MagicMock()
    ctx.session.check_client_capability = MagicMock(return_value=can_elicit)
    ctx.elicit = AsyncMock(return_value=elicit_result)
    return ctx


class TestEnhancedTool(TestTemplate):
    def test_call_invokes_pure_service(self):
        ctx = _make_mock_ctx()
        tool = EnhancedTool(ctx=ctx, input=_Input(n=3), service_fn=_service)
        assert tool.call().doubled == 6

    def test_call_with_override_input(self):
        ctx = _make_mock_ctx()
        tool = EnhancedTool(ctx=ctx, input=_Input(n=3), service_fn=_service)
        result = tool.call(override_input=_Input(n=10))
        assert result.doubled == 20

    def test_can_elicit_true(self):
        tool = EnhancedTool(
            ctx=_make_mock_ctx(can_elicit=True),
            input=_Input(),
            service_fn=_service,
        )
        assert tool.can_elicit is True

    def test_can_elicit_false(self):
        tool = EnhancedTool(
            ctx=_make_mock_ctx(can_elicit=False),
            input=_Input(),
            service_fn=_service,
        )
        assert tool.can_elicit is False

    def test_can_show_app_default_true(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.can_show_app is True

    def test_can_show_app_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.can_show_app is False

    def test_send_text_appends_content(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_text("hello")
        assert len(tool.extra_content) == 1
        block = tool.extra_content[0]
        assert isinstance(block, TextContent)
        assert block.text == "hello"

    def test_send_image_appends_content(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_image(data="abc", mime_type="image/png")
        assert len(tool.extra_content) == 1
        assert tool.extra_content[0].type == "image"

    def test_send_audio_appends_content(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_audio(data="UklGRg==", mime_type="audio/wav")
        assert len(tool.extra_content) == 1
        block = tool.extra_content[0]
        assert isinstance(block, AudioContent)
        assert block.data == "UklGRg=="
        assert block.mimeType == "audio/wav"

    def test_audience_annotations_attached(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_text("for the user", audience=["user"])
        tool.send_image(data="abc", mime_type="image/png", audience=["assistant"])
        tool.send_audio(
            data="def", mime_type="audio/mp3", audience=["user", "assistant"]
        )

        text, image, audio = tool.extra_content
        assert text.annotations is not None
        assert text.annotations.audience == ["user"]
        assert image.annotations is not None
        assert image.annotations.audience == ["assistant"]
        assert audio.annotations is not None
        assert audio.annotations.audience == ["user", "assistant"]

    def test_no_audience_means_no_annotations(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_text("plain")
        assert tool.extra_content[0].annotations is None

    def test_send_app_dual_keys_meta(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_app("ui://test/widget")
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == "ui://test/widget"
        assert meta["ui/resourceUri"] == "ui://test/widget"

    def test_app_meta_none_when_no_app(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.app_meta() is None

    def test_elicit_passthrough(self):
        accepted = AcceptedElicitation(action="accept", data=_Input(n=42))
        ctx = _make_mock_ctx(elicit_result=accepted)
        tool = EnhancedTool(ctx=ctx, input=_Input(), service_fn=_service)
        result = asyncio.run(tool.elicit("confirm?", _Input))
        assert result is accepted


class TestEnhancerRegistry(TestTemplate):
    def test_get_enhancer_returns_registered(self):
        # Importing mcp_server.enhancers.config (at module top) registers the
        # config_show enhancer.
        entry = get_enhancer("config_show")
        assert entry is not None
        assert entry.fallback == "headless"

    def test_duplicate_registration_raises(self):
        # Use a unique service name to avoid conflicting with real services.
        # Clean up after ourselves so the test stays idempotent under repeat runs.
        name = "__test_dup_service"
        _enhancers.pop(name, None)
        try:

            @enhance(name)
            async def _first(tool):  # pragma: no cover
                return None

            with pytest.raises(ValueError, match="Duplicate enhancer"):

                @enhance(name)
                async def _second(tool):  # pragma: no cover
                    return None
        finally:
            _enhancers.pop(name, None)

    def test_get_enhancer_unknown_returns_none(self):
        assert get_enhancer("__definitely_not_a_real_service__") is None


class TestEnhancedToolContextInjection(TestTemplate):
    """Enhanced tools receive Context without exposing it in the input schema.

    Crash-fallback behavior lives in `tests/test_enhancer_fallback.py`.
    """

    def test_context_is_injected_and_not_published_in_schema(self):
        class _CtxIn(BaseModel):
            x: int

        class _CtxOut(BaseModel):
            value: int

        svc_name = "__context_schema_test"

        @service(
            name=svc_name,
            description="test",
            input_model=_CtxIn,
            output_model=_CtxOut,
        )
        def _svc(input: _CtxIn) -> _CtxOut:
            return _CtxOut(value=input.x)

        @enhance(svc_name, fallback="headless")
        async def _enhancer(tool):
            assert tool.can_show_app is True
            return tool.call()

        try:
            entry = next(e for e in get_registry() if e.name == svc_name)
            test_mcp = FastMCP("test_context_schema")
            make_tool(test_mcp, entry)
            tool = test_mcp._tool_manager._tools[svc_name]

            assert tool.context_kwarg == "ctx"
            assert "ctx" not in tool.parameters.get("properties", {})
            assert "ctx" not in tool.parameters.get("required", [])

            result = asyncio.run(tool.run({"x": 7}, context=_make_mock_ctx()))
            assert result.structuredContent == {"value": 7}
        finally:
            _registry[:] = [e for e in _registry if e.name != svc_name]
            _enhancers.pop(svc_name, None)


class TestHeadlessToolErrorPath(TestTemplate):
    """Headless wrappers must propagate service exceptions unchanged.

    There is deliberately no try/except in `_make_headless_tool` - FastMCP
    converts the raised exception into a protocol-level error result. If a
    future refactor swallows the exception, the client would get a bogus
    success payload instead.
    """

    class _In(BaseModel):
        x: int = 0

    class _Out(BaseModel):
        value: int

    def _register_headless_service(self, svc_name, fn):
        service(
            name=svc_name,
            description="test",
            input_model=self._In,
            output_model=self._Out,
        )(fn)

        entry = next(e for e in get_registry() if e.name == svc_name)
        test_mcp = FastMCP("test_headless")
        make_tool(test_mcp, entry)
        tool_fn = test_mcp._tool_manager._tools[svc_name].fn

        def cleanup():
            _registry[:] = [e for e in _registry if e.name != svc_name]

        return tool_fn, cleanup

    def test_service_exception_propagates(self):
        def _exploding(input):
            raise ValueError("service blew up")

        tool_fn, cleanup = self._register_headless_service(
            "__headless_error_test", _exploding
        )
        try:
            with pytest.raises(ValueError, match="service blew up"):
                tool_fn(x=1)
        finally:
            cleanup()

    def test_returns_output_model_instance(self):
        def _ok(input):
            return self._Out(value=input.x + 1)

        tool_fn, cleanup = self._register_headless_service("__headless_ok_test", _ok)
        try:
            result = tool_fn(x=41)
            assert isinstance(result, self._Out)
            assert result.value == 42
        finally:
            cleanup()


class TestBuildCallToolResultTypeGuard(TestTemplate):
    """_build_call_tool_result must reject non-BaseModel results loudly."""

    def test_rejects_non_basemodel_result(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        with pytest.raises(TypeError, match="must be a Pydantic BaseModel"):
            _build_call_tool_result("not a model", tool)  # ty: ignore[invalid-argument-type]

    def test_accepts_basemodel_result(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        result = _build_call_tool_result(_Output(doubled=4), tool)
        assert result.structuredContent == {"doubled": 4}


class TestConfigImageEnhancer(TestTemplate):
    """The config_show enhancer renders a PNG of the config tree."""

    def test_returns_base64_png_data(self):
        data = _render_config_image({"a": 1, "b": {"c": "hello"}})
        assert data is not None
        decoded = base64.b64decode(data)
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

    def test_handles_empty_config(self):
        data = _render_config_image({})
        assert data is not None  # still produces a (small) image

    def test_handles_deeply_nested_config(self):
        """Depth limiter should kick in past 4 levels - should not infinite-recurse."""
        nested: dict = {"a": 1}
        cur = nested
        for i in range(20):
            cur["nested"] = {"depth": i}
            cur = cur["nested"]

        lines = list(_format_config_lines(nested))
        assert any("..." in line for line in lines)

    def test_enhancer_attaches_image_when_capability_allows(self):
        def fake_service(_input: ConfigShowInput) -> ConfigShowResult:
            return ConfigShowResult(config={"hello": "world"})

        ctx = _make_mock_ctx()
        tool: EnhancedTool[ConfigShowInput, ConfigShowResult] = EnhancedTool(
            ctx=ctx, input=ConfigShowInput(), service_fn=fake_service
        )
        asyncio.run(config_show_enhanced(tool))

        assert len(tool.extra_content) == 1
        assert tool.extra_content[0].type == "image"
