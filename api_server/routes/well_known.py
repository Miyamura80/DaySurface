"""Well-known discovery documents for the /mcp endpoint.

The discovery documents that live here:

* **OAuth 2.0 Protected Resource Metadata** (RFC 9728) - tells MCP clients
  where the authorization server is. Required of resource servers by the MCP
  spec (2025-11-25). Served at the path-form URI
  (``/.well-known/oauth-protected-resource/mcp``, tried first because the MCP
  endpoint lives at ``/mcp``) and the root form. Returns 404 when OAuth is not
  configured, so unauthenticated discovery cleanly signals "no authorization
  server" instead of advertising a broken flow.

* **OAuth 2.0 Authorization Server Metadata** (RFC 8414) - mirrors the
  authorization server's (AuthKit's) ``issuer`` / ``authorization_endpoint`` /
  ``token_endpoint`` document at *this* resource server's well-known path.
  Compliant clients follow the RFC 9728 pointer above to the AS and read its
  metadata there, so strictly this is *not* the canonical discovery path. But
  many MCP clients and registry scanners look for RFC 8414 metadata directly on
  the resource server and do **not** follow a redirect to the AS - they then
  report "no OAuth metadata available". An earlier revision answered this path
  with a 307 redirect to AuthKit, but the scanners that motivated it do not
  follow the redirect; serving the document inline as a 200 is what actually
  satisfies them. The upstream document is fetched once and cached.

* **MCP Server Card** (SEP-2127) - pre-connect *discovery*: the identity
  (name, title, description, version, icon) plus the endpoint (``serverUrl`` /
  ``remotes``) and tool surface (``tools[]``) a registry or agent previews
  before opening a transport. Always available (no auth dependency) and served
  with ``Access-Control-Allow-Origin: *`` so any registry crawler can read it.

* **A2A Agent Card** (Agent2Agent spec v0.3.0) - the agent-protocol analogue of
  the Server Card. Served at ``/.well-known/agent-card.json`` so A2A clients and
  orchestrators can discover this agent's identity, endpoint, and skills. Built
  from the same branding config plus the shared service registry (each service
  becomes an A2A skill). Public and cross-origin readable, like the Server Card.

* **API Catalog** (RFC 9727) - a single discovery URL that points agents and
  crawlers at this server's OpenAPI description via an RFC 9264 linkset. Always
  available and CORS-readable, so function-calling agents can find the
  machine-readable API contract without prior knowledge of the spec path.

* **Web Bot Auth key directory**
  (draft-meunier-http-message-signatures-directory) - publishes this agent's
  Ed25519 public signing key(s) as a JWK Set so origins can verify the HTTP
  Message Signatures it sends. Served only when ``WEB_BOT_AUTH_PRIVATE_KEY`` is
  configured (404 otherwise), so a key-less template signals "no signing
  identity" rather than advertising an empty directory.
"""

import base64
import functools
import hashlib
import json
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from api_server.auth.authkit_auth import authkit_domain, mcp_resource_url
from common import global_config
from common.config_models import BrandingConfig, IconConfig
from models.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentProvider,
    A2AAgentSkill,
)
from services import discover_services, get_registry

router = APIRouter(tags=["well-known"])

# AuthKit's RFC 8414 document is effectively static; cache per issuer so the
# resource server does not make an outbound call on every discovery request.
_AS_METADATA_TTL_SECONDS = 3600.0
_as_metadata_cache: dict[str, tuple[float, dict]] = {}

# Media type for the Web Bot Auth key directory (the JWKS variant defined by
# draft-meunier-http-message-signatures-directory).
_WEB_BOT_AUTH_MEDIA_TYPE = "application/http-message-signatures-directory+json"


def _server_version() -> str:
    """Resolve the published package version; fall back when not installed."""
    try:
        return _pkg_version("mcp-template")
    except PackageNotFoundError:
        return "0.0.0"


def _icon(icon: IconConfig) -> dict:
    out: dict = {"src": icon.src, "mimeType": icon.mime_type, "sizes": icon.sizes}
    if icon.theme:
        out["theme"] = icon.theme
    return out


def _tool_surface() -> list[dict]:
    """The LLM-facing MCP tool surface, as ``{name, description}`` summaries.

    Derived from the live service registry so the card can never drift from the
    tools the server actually exposes. Imported lazily: ``mcp_server.server``
    builds the FastMCP singleton at import time, so we defer it to request time
    to keep this route module cheap to import.
    """
    from mcp_server.server import llm_tool_surface  # noqa: PLC0415

    return [{"name": e.name, "description": e.description} for e in llm_tool_surface()]


@router.get("/.well-known/mcp/server-card.json")
def mcp_server_card() -> JSONResponse:
    """SEP-2127 Server Card - pre-connect registry/client discovery.

    Lets agents preview the server before opening a transport: identity
    (``name``/``title``/``description``/``version``/``icons``), where to connect
    (``serverUrl`` + ``remotes``), and the tool surface (``tools[]``).

    No ``$schema`` is emitted: the draft SEP-2127 server-card schema is not yet
    published (the URL 404s), so advertising it would only break validators.
    """
    b = global_config.branding
    card: dict = {
        "name": b.name,
        "version": _server_version(),
        "title": b.title,
        "description": b.description,
        "websiteUrl": b.website_url,
        "repository": {"url": b.repository_url, "source": b.repository_source},
        "icons": [_icon(i) for i in b.icons],
        "tools": _tool_surface(),
    }
    # Only advertise the endpoint when a real public URL is configured.
    # mcp_resource_url() falls back to localhost when MCP_PUBLIC_URL is unset
    # (e.g. a deployed no-OAuth server), and publishing localhost would point
    # registries at a dead endpoint. `serverUrl` (flat, what pre-connect crawlers
    # read) and `remotes` (SEP-2127 / registry shape) name the same endpoint.
    public_url = global_config.MCP_PUBLIC_URL
    if public_url:
        url = public_url.rstrip("/")
        card["serverUrl"] = url
        card["remotes"] = [{"type": "streamable-http", "url": url}]
    # Public branding: any registry crawler (cross-origin) must be able to read it.
    return JSONResponse(card, headers={"Access-Control-Allow-Origin": "*"})


def _agent_endpoint_url(b: BrandingConfig) -> str:
    """Resolve the public host to advertise as the agent's ``url``.

    A2A requires ``url``. This template ships a *discovery* card only - it does
    not implement an A2A wire transport (no JSON-RPC ``message/send``, no
    HTTP+JSON REST binding), so we deliberately omit ``preferredTransport`` and
    point ``url`` at the MCP endpoint, the agent's real machine-facing surface.
    A2A clients use the card to discover that this agent exists and what it can
    do; the MCP host is the honest place to send them today. Prefer a configured
    public host over the branding website, and never the localhost dev default
    (which would point clients at a dead endpoint).
    """
    return (
        global_config.MCP_PUBLIC_URL or global_config.API_PUBLIC_URL or b.website_url
    ).rstrip("/")


def _service_skills() -> list[A2AAgentSkill]:
    """Map each registered service onto an A2A skill (id == service name)."""
    discover_services()
    return [
        A2AAgentSkill(
            id=entry.name,
            name=entry.name,
            description=entry.description,
            tags=["mcp"],
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
        for entry in get_registry()
    ]


@router.get("/.well-known/agent-card.json")
def a2a_agent_card() -> JSONResponse:
    """A2A Agent Card (spec v0.3.0) - pre-connect agent discovery document.

    Discovery/branding only: it advertises this agent's identity and skills so
    A2A registries/clients can find it. We intentionally do not declare a
    ``preferredTransport`` because the template implements no A2A wire transport;
    advertising one would point clients at an endpoint that can't speak it.
    """
    b = global_config.branding
    card = A2AAgentCard(
        name=b.title,
        description=b.description,
        url=_agent_endpoint_url(b),
        version=_server_version(),
        capabilities=A2AAgentCapabilities(),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=_service_skills(),
        # preferred_transport intentionally omitted: see docstring - no A2A wire
        # transport is implemented, so we advertise presence, not a binding.
        provider=A2AAgentProvider(organization=b.title, url=b.website_url),
        icon_url=b.icons[0].src if b.icons else None,
        documentation_url=b.website_url,
    )
    # Public discovery document: any A2A crawler (cross-origin) must read it.
    return JSONResponse(card.to_wire(), headers={"Access-Control-Allow-Origin": "*"})


@router.get("/.well-known/api-catalog")
def api_catalog(request: Request) -> JSONResponse:
    """RFC 9727 API catalog - points agents/crawlers at the OpenAPI description.

    Returns an RFC 9264 linkset whose ``service-desc`` link references this
    server's OpenAPI document, so function-calling agents can discover the
    machine-readable API contract from one well-known URL. Absolute URLs are
    emitted when ``API_PUBLIC_URL`` is configured (matching the OpenAPI
    ``servers`` block); otherwise relative hrefs let the client resolve them
    against the request origin.
    """
    base = (global_config.API_PUBLIC_URL or "").rstrip("/")
    anchor = f"{base}/" if base else "/"
    # Derive the spec path from the live app so the catalog tracks a customized
    # ``openapi_url``; fall back to the FastAPI default if the spec is disabled.
    openapi_path = request.app.openapi_url or "/openapi.json"
    openapi_href = f"{base}{openapi_path}" if base else openapi_path
    catalog = {
        "linkset": [
            {
                "anchor": anchor,
                "service-desc": [
                    {"href": openapi_href, "type": "application/vnd.oai.openapi+json"}
                ],
            }
        ]
    }
    # Public discovery: any agent or registry crawler (cross-origin) must read it.
    return JSONResponse(
        catalog,
        media_type="application/linkset+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _b64url(data: bytes) -> str:
    """base64url-encode without padding (the JOSE convention)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Decode base64url, tolerating missing padding."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _jwk_thumbprint(x: str) -> str:
    """RFC 7638 JWK SHA-256 thumbprint for an Ed25519 (OKP) public key.

    The required members are serialized as compact JSON with lexicographically
    sorted keys, then SHA-256'd and base64url-encoded - this is the ``kid`` form
    the web-bot-auth architecture mandates for Ed25519 keys.
    """
    canonical = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": x},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url(hashlib.sha256(canonical.encode("utf-8")).digest())


@functools.cache
def _signing_key_jwk(seed_b64: str) -> dict:
    """The stable JWK members for a configured Ed25519 seed.

    Everything except the ``nbf``/``exp`` validity window, which is derived per
    response (see ``_web_bot_auth_directory``) so it tracks wall-clock instead of
    freezing to one process's first request. Cached because the derivation (key
    load + RFC 7638 thumbprint) is deterministic for a given seed. Raises
    ``ValueError`` on a malformed seed.
    """
    private_key = Ed25519PrivateKey.from_private_bytes(_b64url_decode(seed_b64))
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    x = _b64url(public_bytes)
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": x,
        "kid": _jwk_thumbprint(x),
        "use": "sig",
        "alg": "EdDSA",
    }


def _web_bot_auth_directory() -> dict:
    """Build the Ed25519 JWK Set for /.well-known/http-message-signatures-directory.

    Returns 404 when no signing key is configured; 500 when the configured seed
    is not a valid 32-byte Ed25519 private key (a deployment misconfiguration we
    surface loudly rather than serving a bad directory).
    """
    # Strip before the emptiness check so a whitespace-only secret reads as
    # "unconfigured" (404), not as a malformed seed (500).
    seed_b64 = (global_config.WEB_BOT_AUTH_PRIVATE_KEY or "").strip()
    if not seed_b64:
        raise HTTPException(status_code=404, detail="Web Bot Auth is not configured")

    try:
        jwk = dict(_signing_key_jwk(seed_b64))
    except ValueError as exc:
        # binascii.Error (bad base64) subclasses ValueError, as does an
        # incorrectly sized seed - both mean the secret is misconfigured.
        raise HTTPException(
            status_code=500,
            detail="WEB_BOT_AUTH_PRIVATE_KEY is not a valid Ed25519 seed",
        ) from exc

    # Derive the validity window per response: it then follows wall-clock and
    # stays consistent no matter which replica - or how long-lived a process -
    # serves the request, rather than freezing to one first-publish instant.
    issued = int(time.time())
    jwk["nbf"] = issued
    jwk["exp"] = issued + global_config.web_bot_auth.key_lifetime_days * 86_400
    return {"keys": [jwk]}


@router.get("/.well-known/http-message-signatures-directory")
def web_bot_auth_directory() -> JSONResponse:
    """Web Bot Auth key directory - the agent's Ed25519 public signing keys.

    Public discovery: any origin verifying this agent's HTTP Message Signatures
    reads it cross-origin, so it is served with ``Access-Control-Allow-Origin:
    *`` and the directory-specific JWKS media type.
    """
    return JSONResponse(
        _web_bot_auth_directory(),
        media_type=_WEB_BOT_AUTH_MEDIA_TYPE,
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _metadata() -> dict:
    domain = authkit_domain()
    if not domain:
        raise HTTPException(status_code=404, detail="OAuth is not configured")
    return {
        "resource": mcp_resource_url(),
        "authorization_servers": [domain],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata_for_mcp() -> dict:
    return _metadata()


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata_root() -> dict:
    return _metadata()


def _authorization_server_metadata() -> dict:
    """Return AuthKit's RFC 8414 metadata document, cached per issuer.

    404 when OAuth is unconfigured (mirrors the PRM routes); 502 when the
    upstream authorization server cannot be reached, so callers see a clear
    "try again" signal rather than a cached or partial document.
    """
    domain = authkit_domain()
    if not domain:
        raise HTTPException(status_code=404, detail="OAuth is not configured")

    now = time.monotonic()
    cached = _as_metadata_cache.get(domain)
    if cached and now - cached[0] < _AS_METADATA_TTL_SECONDS:
        return cached[1]

    url = f"{domain}/.well-known/oauth-authorization-server"
    try:
        resp = httpx.get(url, timeout=5.0, follow_redirects=True)
        resp.raise_for_status()
        metadata = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers a non-JSON body (json.JSONDecodeError subclasses it);
        # treat a malformed upstream document the same as a fetch failure.
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch authorization server metadata",
        ) from exc

    _as_metadata_cache[domain] = (now, metadata)
    return metadata


@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata_for_mcp() -> JSONResponse:
    # Public discovery: registry/scanner crawlers read this cross-origin.
    return JSONResponse(
        _authorization_server_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata_root() -> JSONResponse:
    return JSONResponse(
        _authorization_server_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )
