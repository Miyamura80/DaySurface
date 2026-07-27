"""EnhancedTool - typed wrapper passed to enhancer functions.

Provides capability detection, content emission helpers, and elicitation
passthrough while keeping the pure service callable via `tool.call()`.
"""

import os
from collections.abc import Callable

from mcp.server.elicitation import ElicitationResult
from mcp.server.fastmcp.server import Context
from mcp.types import (
    Annotations,
    AudioContent,
    ClientCapabilities,
    ElicitationCapability,
    ImageContent,
    Role,
    TextContent,
)
from pydantic import BaseModel


def build_app_meta(resource_uri: str) -> dict:
    """Build the ``_meta`` dict declaring an MCP App resource.

    Used both on tool definitions (``tools/list``) and call results. Dual-keys
    the nested spec form and the deprecated flat form for legacy host compat.
    """
    return {
        "ui": {"resourceUri": resource_uri},
        "ui/resourceUri": resource_uri,
    }


class EnhancedTool[TInput: BaseModel, TOutput: BaseModel]:
    """Wrapper around a pure service, passed to enhancer functions.

    Enhancer code uses this to call the pure service, check client capabilities,
    elicit user input, and attach extra content (images, audio, text, app meta)
    to the response.
    """

    def __init__(
        self,
        ctx: Context,
        input: TInput,
        service_fn: Callable[[TInput], TOutput],
    ):
        self._ctx = ctx
        self.input = input
        self._service_fn = service_fn
        self.extra_content: list[TextContent | ImageContent | AudioContent] = []
        self.app_resource_uri: str | None = None
        # Result of the most recent *completed* `call()`, or None if the last
        # `call()` raised (or never ran). Lets the fallback path in
        # `mcp_server._tool_factory` reuse the result instead of re-executing
        # the service (which would duplicate side effects for mutating
        # services) when the enhancer crashes after `call()` completed.
        self.call_result: TOutput | None = None

    def call(self, override_input: TInput | None = None) -> TOutput:
        """Invoke the pure service. Pass `override_input` instead of mutating `tool.input`."""
        # Reset first: a stale stash from an earlier call must not mask a
        # failure of *this* invocation as success in the crash-fallback path.
        self.call_result = None
        result = self._service_fn(
            override_input if override_input is not None else self.input
        )
        self.call_result = result
        return result

    @property
    def can_elicit(self) -> bool:
        """True only when the client DECLARED the elicitation capability.

        Deliberately conservative: under ``stateless_http=True`` (the
        production default) the per-request session never sees the client's
        ``initialize`` params, so this returns False even for
        elicitation-capable hosts - which is correct, because in-band
        elicitation cannot complete stateless (the client's response POST is
        uncorrelatable; ``ctx.elicit()`` would hang). Do NOT change this to
        attempt-when-unknown. See the transport comment in
        ``mcp_server/server.py``.
        """
        return self._ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        )

    @property
    def can_show_app(self) -> bool:
        """Best-effort check; spec lacks a standard capability for app rendering.

        Defaults to True. Set MCP_DISABLE_APPS=1 to force-skip on misbehaving clients.
        See mcp_server/MCP_UI_EDGE_CASES.md C2.
        """
        return os.environ.get("MCP_DISABLE_APPS", "") == ""

    async def elicit(self, message: str, schema: type[BaseModel]) -> ElicitationResult:
        """Ask the user for input mid-tool. Schema must be a flat primitive-only BaseModel."""
        return await self._ctx.elicit(message=message, schema=schema)

    def send_text(
        self,
        text: str,
        audience: list[Role] | None = None,
    ) -> None:
        annotations = _annotations(audience)
        self.extra_content.append(
            TextContent(type="text", text=text, annotations=annotations)
        )

    def send_image(
        self,
        data: str,
        mime_type: str,
        audience: list[Role] | None = None,
    ) -> None:
        """Append a base64-encoded image to the response."""
        annotations = _annotations(audience)
        self.extra_content.append(
            ImageContent(
                type="image", data=data, mimeType=mime_type, annotations=annotations
            )
        )

    def send_audio(
        self,
        data: str,
        mime_type: str,
        audience: list[Role] | None = None,
    ) -> None:
        """Append a base64-encoded audio clip to the response."""
        annotations = _annotations(audience)
        self.extra_content.append(
            AudioContent(
                type="audio", data=data, mimeType=mime_type, annotations=annotations
            )
        )

    def send_app(self, resource_uri: str) -> None:
        """Attach an MCP App resource to the response. Caller-resolved `ui://` URI."""
        self.app_resource_uri = resource_uri

    def app_meta(self) -> dict | None:
        """Build the _meta dict for app attachment, or None if no app was sent."""
        if self.app_resource_uri is None:
            return None
        return build_app_meta(self.app_resource_uri)


def _annotations(audience: list[Role] | None):
    if audience is None:
        return None
    return Annotations(audience=audience)
