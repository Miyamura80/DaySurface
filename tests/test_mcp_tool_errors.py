"""SDK 2.x behavior shims: error-text pass-through and the elicitation gate."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from google.auth.exceptions import MalformedError
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from api_server.auth.unified_auth import AuthenticatedUser
from mcp_server._tool_errors import model_facing_message
from mcp_server._tool_factory import make_tool
from mcp_server.enhancers.base import client_can_elicit
from mcp_server.server import _DaySurfaceMCPServer
from models.pdf_forms import SignaturePlacement
from services import InvalidArgument, ModelFacingError, ServiceEntry
from services._gmail_history import HistoryGoneError
from services._gmail_retry import GmailRateLimitedError
from services.gmail_reply_svc import DuplicateReplyError
from services.gmail_svc import GmailNotConnectedError
from services.image_proxy import ImageFetchError
from services.pdf_documents_repo import PdfDocumentNotFoundError
from services.pdf_inspect import PdfCorruptError
from services.pdf_ports import PdfPortNotRegisteredError
from src.utils.current_user import reset_current_user, set_current_user
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


def _raised_in(module: str, exc: BaseException) -> BaseException:
    """Return ``exc`` carrying a traceback whose innermost frame is ``module``."""
    namespace = {"__name__": module, "exc": exc}
    try:
        exec("raise exc", namespace)  # noqa: S102 - builds a frame in `module`
    except BaseException as raised:  # noqa: BLE001
        # Test helper: re-catches exactly the exception it just raised.
        return raised
    raise AssertionError("unreachable")


class TestModelFacingMessage(TestTemplate):
    def test_model_facing_errors_keep_their_text(self):
        assert model_facing_message(InvalidArgument("bad arg")) == "bad arg"
        gmail = model_facing_message(GmailNotConnectedError("u1"))
        assert gmail is not None and gmail.startswith("No Gmail account")

    @pytest.mark.parametrize(
        ("exc", "needle"),
        [
            (GmailRateLimitedError(side_effects_possible=False), "retry this tool"),
            (DuplicateReplyError("already replied"), "already replied"),
            (PdfDocumentNotFoundError("doc-1"), "pdf_open"),
            (PdfCorruptError(), "corrupt"),
        ],
    )
    def test_daysurface_recovery_text_survives(self, exc, needle):
        # Product errors whose message is the model's recovery script.
        assert isinstance(exc, ModelFacingError)
        message = model_facing_message(exc)
        assert message is not None and needle in message

    def test_billing_http_refusal_keeps_its_text(self):
        quota = _raised_in(
            "api_server.billing.limits", HTTPException(status_code=429, detail="quota")
        )
        assert model_facing_message(quota) == "quota"
        structured = _raised_in(
            "api_server.billing.paywall",
            HTTPException(status_code=402, detail={"message": "pay up", "x": 1}),
        )
        assert model_facing_message(structured) == "pay up"

    def test_http_exception_from_elsewhere_stays_sanitized(self):
        assert model_facing_message(HTTPException(status_code=500, detail="x")) is None
        foreign = _raised_in(
            "starlette.routing", HTTPException(status_code=404, detail="/srv/x")
        )
        assert model_facing_message(foreign) is None

    @pytest.mark.parametrize(
        "exc",
        [
            # google-auth's errors subclass ValueError and quote key material.
            MalformedError("could not deserialize key data: -----BEGIN"),
            # A filesystem refusal names a server path.
            PermissionError(13, "Permission denied", "/srv/app/secrets/token.json"),
            ValueError("bad arg"),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            # First-party module, but wraps third-party (httpx) text.
            ImageFetchError("image fetch failed: connect to 10.0.0.7 refused"),
            PdfPortNotRegisteredError("no adapter"),
            HistoryGoneError("history gone"),
            _ThirdPartyError("dsn=secret"),
            RuntimeError("internal"),
        ],
    )
    def test_everything_else_stays_sanitized(self, exc):
        assert model_facing_message(exc) is None

    def test_none(self):
        assert model_facing_message(None) is None


class TestServerErrorRelabel(TestTemplate):
    def _server_with(self, exc: Exception) -> _DaySurfaceMCPServer:
        server = _DaySurfaceMCPServer("errors-test")

        @server.tool(name="boom")
        def boom() -> str:
            raise exc

        return server

    def test_model_facing_failure_reaches_model(self):
        server = self._server_with(InvalidArgument("Pass 'to' explicitly."))
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "Pass 'to' explicitly." in str(excinfo.value)

    @pytest.mark.parametrize(
        "exc",
        [
            _ThirdPartyError("dsn=secret"),
            PermissionError(13, "Permission denied", "/srv/secret/path"),
            MalformedError("secret key bytes"),
        ],
    )
    def test_other_failures_are_sanitized(self, exc):
        server = self._server_with(exc)
        with pytest.raises(UnexpectedToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert "secret" not in str(excinfo.value)


class TestWrapperRefusals(TestTemplate):
    """The tool wrappers' own refusals reach the model as ToolErrors."""

    def _server(self) -> _DaySurfaceMCPServer:
        server = _DaySurfaceMCPServer("wrapper-test")
        entry = ServiceEntry(
            name="place",
            description="place",
            # Its model validator (field_name XOR page/x/y) runs only when the
            # wrapper constructs the input, after the SDK's schema check.
            input_model=SignaturePlacement,
            output_model=SignaturePlacement,
            func=lambda inp: inp,
        )
        make_tool(server, entry)
        return server

    def test_input_model_validator_reaches_model(self):
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(
                self._server().call_tool(
                    "place", {"field_name": "sig", "page": 1, "x": 1.0, "y": 1.0}
                )
            )
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "not both" in str(excinfo.value)

    def test_scope_refusal_reaches_model(self):
        user = AuthenticatedUser(
            user_id="u-ro", auth_method="api_key", scopes=["services:read"]
        )
        token = set_current_user(user)
        try:
            with pytest.raises(ToolError) as excinfo:
                asyncio.run(self._server().call_tool("place", {"field_name": "sig"}))
        finally:
            reset_current_user(token)
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "services:execute" in str(excinfo.value)


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
