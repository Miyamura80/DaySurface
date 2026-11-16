"""Every MCP tool declares read/write/destructive/open-world hints, and calls are logged."""

import asyncio

from loguru import logger as log

from mcp_server.server import mcp
from mcp_server.tool_annotations import TOOL_HINTS
from services import get_registry
from tests.test_template import TestTemplate


def _tools_by_name():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


class TestToolAnnotations(TestTemplate):
    def test_every_tool_has_explicit_hints(self):
        missing = []
        for name, tool in _tools_by_name().items():
            a = tool.annotations
            if (
                a is None
                or a.readOnlyHint is None
                or a.destructiveHint is None
                or a.openWorldHint is None
            ):
                missing.append(name)
        assert not missing, (
            f"Add these tools to TOOL_HINTS in mcp_server/tool_annotations.py: {missing}"
        )

    def test_hint_table_has_no_stale_entries(self):
        stale = set(TOOL_HINTS) - set(_tools_by_name())
        assert not stale, f"TOOL_HINTS lists tools that are not registered: {stale}"

    def test_mutating_services_are_not_read_only(self):
        tools = _tools_by_name()
        for entry in get_registry():
            tool = tools.get(entry.name)
            if entry.mutating and tool is not None:
                assert tool.annotations is not None
                assert tool.annotations.readOnlyHint is False, entry.name

    def test_send_tools_are_destructive_and_open_world(self):
        tools = _tools_by_name()
        for name in ("gmail_send", "gmail_composer.send"):
            a = tools[name].annotations
            assert a is not None
            assert a.readOnlyHint is False
            assert a.destructiveHint is True
            assert a.openWorldHint is True

    def test_irreversible_signing_is_destructive(self):
        tools = _tools_by_name()
        for name in ("pdf_signer.sign", "pdf_request_signature"):
            a = tools[name].annotations
            assert a is not None
            assert a.destructiveHint is True, name

    def test_reads_are_read_only(self):
        tools = _tools_by_name()
        for name in ("gmail_list_inbox", "gmail_get_thread", "inbox_search"):
            a = tools[name].annotations
            assert a is not None
            assert a.readOnlyHint is True, name


class TestToolCallLogging(TestTemplate):
    def test_tool_call_logs_name_and_outcome_without_arguments(self):
        lines: list[str] = []
        sink_id = log.add(lambda m: lines.append(str(m)), level="INFO")
        try:
            asyncio.run(
                mcp.call_tool("gmail_get_focused_email", {"user_id": "secret-arg"})
            )
        except Exception:  # noqa: BLE001
            # Any outcome is fine here (no user is bound outside a request);
            # the test only checks what gets logged.
            pass
        finally:
            log.remove(sink_id)
        call_lines = [line for line in lines if "MCP tool call" in line]
        assert call_lines, lines
        assert "gmail_get_focused_email" in call_lines[-1]
        assert " ok " in call_lines[-1] or " error " in call_lines[-1]
        assert "secret-arg" not in call_lines[-1]
