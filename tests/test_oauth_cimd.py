"""Client ID Metadata Documents for DaySurface's own OAuth server."""

from unittest.mock import MagicMock, patch

import pytest
from mcp.shared.auth import InvalidRedirectUriError
from pydantic import AnyUrl

from api_server.oauth import cimd
from tests.oauth_server_fixture import (
    CLIENT_ID,
    client_document,
)
from tests.test_template import TestTemplate


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

    def test_confidential_clients_are_refused(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(
                CLIENT_ID,
                client_document(token_endpoint_auth_method="private_key_jwt"),
            )

    def test_private_hosts_are_never_fetched(self):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.resolve_client("https://127.0.0.1/meta.json")

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

    def test_slow_document_is_abandoned_at_the_deadline(self):
        response = MagicMock(status_code=200, headers={})
        response.iter_raw.return_value = iter([b"{", b" ", b" ", b" "])
        http = MagicMock()
        http.__enter__.return_value.stream.return_value.__enter__.return_value = (
            response
        )
        clock = iter(range(0, 100, 3))  # each read takes three seconds
        with (
            patch.object(cimd, "validate_and_pin", return_value=MagicMock(headers={})),
            patch.object(cimd, "build_client", return_value=http),
            patch.object(cimd.time, "monotonic", side_effect=lambda: next(clock)),
            pytest.raises(cimd.ClientMetadataError, match="too long"),
        ):
            cimd._fetch_document(CLIENT_ID)

    def test_compressed_document_is_refused(self):
        response = MagicMock(status_code=200, headers={"content-encoding": "gzip"})
        http = MagicMock()
        http.__enter__.return_value.stream.return_value.__enter__.return_value = (
            response
        )
        with (
            patch.object(cimd, "validate_and_pin", return_value=MagicMock(headers={})),
            patch.object(cimd, "build_client", return_value=http),
            pytest.raises(cimd.ClientMetadataError, match="compressed"),
        ):
            cimd._fetch_document(CLIENT_ID)
