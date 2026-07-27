"""Convert connect-required service failures into SEP-1036 URL-mode elicitation.

When a tool is called for a user who must first complete an external connect
flow (the service raised a ``ConnectRequiredError`` subclass - Gmail linking
today), the MCP layer upgrades it into the spec's URL-elicitation-required
error (JSON-RPC code -32042, spec 2025-11-25) whose ``data.elicitations``
carries the flow's authorization URL. Hosts that support URL-mode elicitation
open the consent flow natively and may retry the original call; the conversion
is MCP-only, so CLI/API consumers of the same services still see the plain
exception.

This module is feature-agnostic: everything integration-specific (the auth
URL, the elicitation message, the textual recovery script) travels on the
``ConnectRequiredError`` contract defined in ``services/__init__.py``.

Conversion policy (the spec says servers SHOULD check client capabilities
before sending mode-specific requests):

- Client declared elicitation WITHOUT url mode -> no conversion. The original
  exception (whose message contains the manual recovery script) crosses the
  wire as an ``isError`` tool result.
- Client declared ``elicitation.url`` -> convert.
- Client capabilities unknown -> convert. This is the production norm: with
  ``stateless_http=True`` every tools/call arrives on a fresh transport that
  never saw the client's ``initialize`` params, so ``client_params`` is unset
  and capabilities are unknowable. Converting stays safe for hosts that don't
  understand -32042 because the error ``message`` duplicates the full textual
  recovery script including the authorization URL itself.

Known limitation under stateless HTTP: the server cannot send
``notifications/elicitation/complete`` (there is no persistent client stream
after the request ends), so hosts retry manually once the user finishes
consent - the spec anticipates exactly this ("if a completion notification
never arrives, clients SHOULD provide a manual way for the user to continue").
"""

import secrets
from typing import NoReturn

from loguru import logger as log
from mcp.server.session import ServerSession
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import ElicitRequestURLParams

from services import ConnectRequiredError


def _client_url_support(session: ServerSession | None) -> bool | None:
    """Tri-state URL-elicitation support: True/False when declared, None when unknown.

    ``None`` (unknown) is the norm in production stateless-HTTP mode, where the
    per-request session never observed the client's ``initialize`` params.
    """
    params = session.client_params if session is not None else None
    if params is None:
        return None
    elicitation = params.capabilities.elicitation
    if elicitation is None:
        return False
    # Spec back-compat rule: a bare `elicitation: {}` capability means
    # form-mode only, so absence of the `url` member is a declared inability.
    return elicitation.url is not None


def reraise_with_elicitation(
    session: ServerSession | None, exc: ConnectRequiredError
) -> NoReturn:
    """Raise the SEP-1036 error for ``exc`` when possible, else re-raise ``exc``.

    ``exc`` propagates unconverted (its self-recovering message becomes the
    ``isError`` tool-result text) when the client declared it cannot do
    URL-mode elicitation, or when the connect flow is unconfigured in this
    deployment (``build_auth_url()`` returned None).
    """
    if _client_url_support(session) is False:
        raise exc
    auth_url = exc.build_auth_url()
    if auth_url is None:
        log.debug(
            "{} has no auth URL (connect flow unconfigured); using textual recovery",
            type(exc).__name__,
        )
        raise exc
    raise UrlElicitationRequiredError(
        [
            ElicitRequestURLParams(
                mode="url",
                message=exc.elicitation_message,
                url=auth_url,
                # Opaque + unique per server as the spec requires; the actual
                # user binding travels inside the auth URL (e.g. Gmail's
                # signed `state` parameter).
                elicitationId=f"connect-{secrets.token_urlsafe(8)}",
            )
        ],
        message=(
            f"{exc} If URL-mode elicitation is unsupported, present this "
            f"authorization URL to the user yourself and retry after "
            f"they consent: {auth_url}"
        ),
    ) from exc
