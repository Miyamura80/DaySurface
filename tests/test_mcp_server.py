"""Tests for MCP server tool registration."""

import asyncio
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from mcp_server.server import _register_app_resource, mcp
from services import get_registry
from tests.test_template import TestTemplate


class TestMCPServer(TestTemplate):
    def test_server_imports(self):
        assert mcp is not None

    def test_all_services_registered_as_tools(self):
        # Importing mcp_server.server (at module top) triggers service module
        # imports + registration. Some services are deliberately not exposed
        # through the default MCP surface, but they remain available to
        # CLI/API transports.
        registry = get_registry()
        service_names = {entry.name for entry in registry}

        assert "greet" in service_names
        assert "config_show" in service_names
        assert "doctor" in service_names

    def test_registry_entries_have_models(self):
        for entry in get_registry():
            assert entry.input_model is not None
            assert entry.output_model is not None
            assert entry.func is not None
            assert entry.description

    def test_default_mcp_surface_excludes_admin_and_demo_tools(self):
        tools = mcp._tool_manager._tools
        for tool_name in ("config_get", "config_set", "config_show", "doctor", "greet"):
            assert tool_name not in tools

    def test_enhanced_tools_publish_output_schema(self):
        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.output_schema is not None, f"{tool_name} missing outputSchema"

    def test_enhanced_output_schema_matches_output_model(self):
        # _patch_output_schema writes into FastMCP's private registry; assert
        # the published schema is byte-for-byte the service output model's
        # schema, not just non-None, so an SDK upgrade that breaks the patch
        # path fails loudly here.
        by_name = {e.name: e for e in get_registry()}
        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools[tool_name]
            output_model = by_name[tool_name].output_model
            assert issubclass(output_model, BaseModel)
            expected = output_model.model_json_schema()
            assert tool.output_schema == expected, (
                f"{tool_name} outputSchema diverged from its output_model"
            )

    def test_app_rendering_tools_declare_ui_resource_in_tools_list(self):
        # Per the MCP Apps spec, tools that render an app must advertise the
        # ui:// resource in their tools/list _meta so hosts can pre-fetch the
        # HTML and apply CSP before the first call (found by MCPJam's
        # `apps conformance` check).
        app_tools = {
            "gmail_compose",
            "gmail_update_draft",
            "gmail_reply_to_thread",
            "gmail_curate_inbox",
            "gmail_get_thread",
        }
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        for name in app_tools:
            meta = tools[name].meta
            assert meta is not None, f"{name} missing _meta"
            uri = meta["ui"]["resourceUri"]
            assert uri.startswith("ui://mymcp/"), f"{name} bad resourceUri: {uri}"
            # Deprecated flat key kept for legacy host compat.
            assert meta["ui/resourceUri"] == uri

        # Headless tools must not grow UI metadata.
        assert tools["gmail_send"].meta is None
        # App-only companion tools keep visibility-only metadata.
        assert tools["gmail_inbox.refresh"].meta == {"ui": {"visibility": ["app"]}}

    def test_enhanced_tools_do_not_publish_context_as_input(self):
        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.context_kwarg == "ctx"
            assert "ctx" not in tool.parameters.get("properties", {})
            assert "ctx" not in tool.parameters.get("required", [])

    def test_default_factory_fields_resolve_to_concrete_defaults(self):
        # Regression: inspect.signature() renders a Pydantic default_factory
        # field with the private <factory> sentinel. FastMCP copies each
        # parameter default verbatim into the tool's argument model, so an
        # unresolved sentinel is handed back as the value when the caller omits
        # the field, failing validation ("Input should be a valid list ...
        # input_value=<factory>") and making the field effectively required.
        # _apply_tool_signature must resolve the factory to a concrete default.
        for tool_name, tool in mcp._tool_manager._tools.items():
            arg_model = tool.fn_metadata.arg_model
            for name, field in arg_model.model_fields.items():
                default_repr = repr(field.default)
                assert "<factory>" not in default_repr, (
                    f"{tool_name}.{name} leaks the default_factory sentinel "
                    f"into its tool schema: {default_repr}"
                )

    def test_reply_to_thread_attachments_optional_without_factory_sentinel(self):
        # Concrete end-to-end guard for the reported gmail_reply_to_thread bug:
        # validating the tool's argument model with attachments omitted must
        # yield a real empty list, and building the service input from it must
        # not raise.
        from services.gmail_drafts_svc import GmailReplyInput  # noqa: PLC0415

        arg_model = mcp._tool_manager._tools[
            "gmail_reply_to_thread"
        ].fn_metadata.arg_model
        validated = arg_model.model_validate({"thread_id": "t1"}).model_dump()
        assert validated["attachments"] == []
        # Downstream construction that previously raised the validation error.
        assert GmailReplyInput(**validated).attachments == []

    def test_resolve_signature_default_covers_all_factory_forms(self):
        # Unit-covers _resolve_signature_default across every branch, including
        # the validated-data factory form no shipped input model exercises yet.
        import inspect  # noqa: PLC0415

        from pydantic import Field  # noqa: PLC0415

        from mcp_server._tool_factory import _resolve_signature_default  # noqa: PLC0415

        class _Model(BaseModel):
            plain: str = "x"
            zero: list[str] = Field(default_factory=list)
            validated: str = Field(default_factory=lambda data: "derived")

        params = inspect.signature(_Model).parameters
        fields = _Model.model_fields

        # No FieldInfo, or a field without a factory -> keep the signature default.
        assert _resolve_signature_default(params["plain"], None) == "x"
        assert _resolve_signature_default(params["plain"], fields["plain"]) == "x"

        # Zero-arg factory resolves and yields a fresh object each call.
        first = _resolve_signature_default(params["zero"], fields["zero"])
        second = _resolve_signature_default(params["zero"], fields["zero"])
        first.append("mutated")
        assert first == ["mutated"] and second == []

        # Validated-data factory form is dispatched by arity, not a caught error.
        assert (
            _resolve_signature_default(params["validated"], fields["validated"])
            == "derived"
        )


class TestMCPServerIntegration(TestTemplate):
    """End-to-end integration tests calling tools through the registered FastMCP wrapper."""

    def test_app_resources_registered_and_serve_html(self):
        resources = asyncio.run(mcp.list_resources())
        uris = {str(r.uri) for r in resources}
        # gmail_composer / gmail_inbox apps are added in later phases; here we
        # only assert that whatever ui:// resources are registered serve HTML.
        ui_uris = [u for u in uris if u.startswith("ui://mymcp/")]
        assert ui_uris, "expected at least one ui://mymcp/ resource registered"
        for uri in ui_uris:
            contents = list(asyncio.run(mcp.read_resource(uri)))
            assert len(contents) == 1
            text = str(contents[0].content)
            assert text.lstrip().lower().startswith("<!doctype html>")

    def test_missing_app_build_serves_stub_comment(self):
        # Edge case A1 (mcp_server/MCP_UI_EDGE_CASES.md): an app dir without a
        # built dist/mcp-app.html must serve an HTML comment stub, not crash.
        test_mcp = FastMCP("test_stub")
        missing = Path("/nonexistent/test_app/dist/mcp-app.html")
        _register_app_resource(
            test_mcp, "ui://mymcp/test_stub_app", missing, "test_stub_app"
        )

        contents = list(asyncio.run(test_mcp.read_resource("ui://mymcp/test_stub_app")))
        assert len(contents) == 1
        text = str(contents[0].content)
        assert text.startswith("<!--")
        assert "test_stub_app not built" in text
        assert "make build_apps" in text

    def test_model_visible_focus_tool_returns_pydantic_model_directly(self):
        tool_fn = mcp._tool_manager._tools["gmail_get_focused_email"].fn
        result = tool_fn(user_id="test-user")
        assert result.focused is False
