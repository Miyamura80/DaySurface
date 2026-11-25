"""Client ID Metadata Documents for DaySurface's own OAuth server."""

import hashlib
from unittest.mock import MagicMock, patch

import httpx
import pytest
from mcp.shared.auth import InvalidRedirectUriError
from pydantic import AnyUrl

from api_server.oauth import cimd, cimd_document, signing
from api_server.oauth.cimd_document import FetchedDocument, TransientFetchError
from api_server.server import Sha256SessionMiddleware
from services._ssrf_guard import PinnedTarget, build_async_client
from tests.oauth_server_fixture import (
    CLIENT_ID,
    client_document,
)
from tests.test_template import TestTemplate

_TARGET = PinnedTarget(url="https://192.0.2.1/meta.json")  # TEST-NET-1


class TestClientMetadataDocuments(TestTemplate):
    def test_url_shape(self):
        assert cimd.is_metadata_document_client_id(CLIENT_ID)
        for bad in (
            "http://client.test.example/meta.json",
            "https://client.test.example/",
            "https://client.test.example",
            "https://client.test.example/meta.json#frag",
            "https://client.test.example/meta.json?x=1",
            "https://user@client.test.example/meta.json",
            "https://client.test.example/a/../meta.json",
            "not a url",
        ):
            assert not cimd.is_metadata_document_client_id(bad), bad

    def test_document_must_name_its_own_url(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(
                CLIENT_ID, client_document(client_id="https://evil.example/m.json")
            )

    def test_redirect_uris_must_be_https_or_loopback(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(
                CLIENT_ID, client_document(redirect_uris=["http://evil.example/cb"])
            )
        client = cimd.parse_document(
            CLIENT_ID,
            client_document(redirect_uris=["http://127.0.0.1:33418/callback"]),
        )
        assert client.redirect_uris
        assert str(client.redirect_uris[0]).startswith("http://127.0.0.1")

    def test_redirect_uris_are_capped(self):
        many = [f"https://client.test.example/cb{n}" for n in range(11)]
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.parse_document(CLIENT_ID, client_document(redirect_uris=many))
        assert cimd.parse_document(
            CLIENT_ID, client_document(redirect_uris=many[:10])
        ).redirect_uris

    def test_confidential_clients_are_refused(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(
                CLIENT_ID,
                client_document(token_endpoint_auth_method="private_key_jwt"),
            )

    def test_chatgpt_document_negotiates_public_authentication(self):
        # ChatGPT's shape: prefers private_key_jwt but lists none as supported.
        client = cimd.parse_document(
            CLIENT_ID,
            client_document(
                token_endpoint_auth_method="private_key_jwt",
                token_endpoint_auth_methods_supported=["none", "private_key_jwt"],
                jwks_uri="https://client.test.example/jwks.json",
            ),
        )
        assert client.token_endpoint_auth_method == "none"
        assert client.client_secret is None

    def test_supported_methods_list_is_authoritative(self):
        # The list overrides the singular preference in both directions.
        with pytest.raises(cimd.ClientMetadataError, match="only public clients"):
            cimd.parse_document(
                CLIENT_ID,
                client_document(
                    token_endpoint_auth_method="none",
                    token_endpoint_auth_methods_supported=["private_key_jwt"],
                ),
            )
        assert cimd.parse_document(
            CLIENT_ID, client_document(token_endpoint_auth_methods_supported=["none"])
        )

    @pytest.mark.parametrize(
        "methods", [None, "none", [], ["none", 42], {"none": True}]
    )
    def test_malformed_supported_methods_are_refused(self, methods):
        with pytest.raises(cimd.ClientMetadataError, match="non-empty array"):
            cimd.parse_document(
                CLIENT_ID,
                client_document(token_endpoint_auth_methods_supported=methods),
            )

    def test_malformed_redirect_uri_is_a_metadata_error(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(
                CLIENT_ID, client_document(redirect_uris=["https://[::1/callback"])
            )

    def test_non_ascii_client_ids_are_refused(self):
        # Cyrillic "і" in a look-alike host.
        assert not cimd.is_metadata_document_client_id(
            "https://cl\u0456ent.test.example/meta.json"
        )
        assert not cimd.is_metadata_document_client_id(
            "https://client.test.example/" + "a" * 2000
        )

    def test_loopback_redirects_match_on_any_port(self):
        client = cimd.parse_document(
            CLIENT_ID, client_document(redirect_uris=["http://127.0.0.1/callback"])
        )
        assert client.validate_redirect_uri(AnyUrl("http://127.0.0.1:53817/callback"))
        for bad in ("http://127.0.0.1:53817/other", "http://localhost:53817/callback"):
            with pytest.raises(InvalidRedirectUriError):
                client.validate_redirect_uri(AnyUrl(bad))


class _Body(httpx.AsyncByteStream):
    """A streamed body, as a real server sends it (``content=`` is pre-read)."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data


def _response(status: int, body: bytes = b"", **headers: str) -> httpx.Response:
    return httpx.Response(status, stream=_Body(body), headers=headers)


def _mock_client(handler):
    """build_async_client's stand-in: the same flags over a mock transport."""

    def build(*, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )

    return build


async def _fetch(handler) -> FetchedDocument:
    with patch.object(cimd_document, "build_async_client", _mock_client(handler)):
        return await cimd_document.fetch_document(_TARGET, timeout=1.0)


class TestFetchDocument(TestTemplate):
    """What a response means: a document, a definite failure or a transient one."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.mark.anyio
    async def test_document_and_cache_control_are_returned(self):
        fetched = await _fetch(
            lambda _: _response(200, b"{}", **{"Cache-Control": "max-age=60"})
        )
        assert fetched == FetchedDocument(b"{}", "max-age=60")

    @pytest.mark.anyio
    async def test_compressed_document_is_refused(self):
        with pytest.raises(cimd.ClientMetadataError, match="compressed") as info:
            await _fetch(
                lambda _: _response(200, b"{}", **{"Content-Encoding": "gzip"})
            )
        assert not isinstance(info.value, TransientFetchError)

    @pytest.mark.anyio
    async def test_oversized_document_is_refused(self):
        big = b"x" * (cimd_document._MAX_DOCUMENT_BYTES + 1)
        with pytest.raises(cimd.ClientMetadataError, match="too large") as info:
            await _fetch(lambda _: _response(200, big))
        assert not isinstance(info.value, TransientFetchError)

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [301, 400, 404, 410, 429])
    async def test_other_statuses_are_definite(self, status):
        with pytest.raises(cimd.ClientMetadataError) as info:
            await _fetch(lambda _: httpx.Response(status))
        assert not isinstance(info.value, TransientFetchError)

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_server_errors_are_transient(self, status):
        with pytest.raises(TransientFetchError):
            await _fetch(lambda _: httpx.Response(status))

    @pytest.mark.anyio
    async def test_network_errors_are_transient(self):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(TransientFetchError):
            await _fetch(refuse)

    @pytest.mark.anyio
    async def test_redirects_are_not_followed(self):
        seen: list[str] = []

        def redirect(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})

        with pytest.raises(cimd.ClientMetadataError, match="HTTP 302"):
            await _fetch(redirect)
        assert seen == [_TARGET.url]

    def test_async_client_keeps_the_hardening(self):
        client = build_async_client(timeout=1.0)
        assert client.trust_env is False
        assert client.follow_redirects is False


class TestSignedValues(TestTemplate):
    def test_signatures_use_sha256(self):
        signer = signing._serializer("test").make_signer()
        assert signer.digest_method is hashlib.sha256

    def test_session_cookie_uses_sha256(self):
        middleware = Sha256SessionMiddleware(MagicMock(), secret_key="k" * 32)
        assert middleware.signer.digest_method is hashlib.sha256
