"""SDK 2.x behavior shims: error-text pass-through and the elicitation gate."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from mcp_server._tool_errors import model_facing_message
from mcp_server.enhancers.base import client_can_elicit
from mcp_server.server import _DaySurfaceMCPServer
from services._gmail_retry import GmailRateLimitedError
from services.gmail_reply_svc import DuplicateReplyError
from services.gmail_svc import GmailNotConnectedError
from services.pdf_documents_repo import PdfDocumentNotFoundError
from tests.test_template import TestTemplate

# Stands in for a foreign crash (SQLAlchemy, HTTP client, ...): an exception
# class whose defining module is outside the first-party packages.
_ThirdPartyError = type(
    "OperationalError", (Exception,), {"__module__": "sqlalchemy.exc"}
)


def _fake_session(*, can_send: bool, declared: bool) -> SimpleNamespace:
    return SimpleNamespace(
        can_send_request=can_send,
        check_client_capability=lambda _capability: declared,
    )


class TestModelFacingMessage(TestTemplate):
    def test_first_party_and_builtin_errors_keep_their_text(self):
        assert model_facing_message(PermissionError("scope")) == "scope"
        assert model_facing_message(ValueError("bad arg")) == "bad arg"
        gmail = model_facing_message(GmailNotConnectedError("u1"))
        assert gmail is not None and gmail.startswith("No Gmail account")
        assert (
            model_facing_message(HTTPException(status_code=429, detail="quota"))
            == "quota"
        )

    @pytest.mark.parametrize(
        ("exc", "needle"),
        [
            (GmailRateLimitedError(side_effects_possible=False), "retry this tool"),
            (DuplicateReplyError("already replied"), "already replied"),
            (PdfDocumentNotFoundError("doc-1"), "pdf_open"),
        ],
    )
    def test_daysurface_recovery_text_survives(self, exc, needle):
        # Product errors whose message is the model's recovery script.
        message = model_facing_message(exc)
        assert message is not None and needle in message

    def test_third_party_crashes_stay_sanitized(self):
        assert model_facing_message(_ThirdPartyError("dsn=secret")) is None
        assert model_facing_message(RuntimeError("internal")) is None
        assert model_facing_message(None) is None


class TestTemplateServerErrorRelabel(TestTemplate):
    def _server_with(self, exc: Exception) -> _DaySurfaceMCPServer:
        server = _DaySurfaceMCPServer("errors-test")

        @server.tool(name="boom")
        def boom() -> str:
            raise exc

        return server

    def test_first_party_failure_reaches_model(self):
        server = self._server_with(PermissionError("services:execute required"))
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "services:execute required" in str(excinfo.value)

    def test_third_party_failure_is_sanitized(self):
        server = self._server_with(_ThirdPartyError("dsn=secret"))
        with pytest.raises(UnexpectedToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert "secret" not in str(excinfo.value)


class TestClientCanElicit(TestTemplate):
    def test_requires_declared_capability_and_back_channel(self):
        assert client_can_elicit(_fake_session(can_send=True, declared=True))

    def test_no_back_channel_means_no_elicitation(self):
        # Every 2026-07-28 request and stateless legacy HTTP.
        assert not client_can_elicit(_fake_session(can_send=False, declared=True))

    def test_undeclared_capability_means_no_elicitation(self):
        assert not client_can_elicit(_fake_session(can_send=True, declared=False))

    def test_no_session(self):
        assert not client_can_elicit(None)
        assert not client_can_elicit(SimpleNamespace(can_send_request=False))
