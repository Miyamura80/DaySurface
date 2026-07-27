"""Tier S: API route tests for agentic payments endpoints.

Tests the full HTTP flow via FastAPI TestClient, catching wrong status
codes, missing error details, and broken request validation.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from api_server.auth.unified_auth import AuthenticatedUser, get_authenticated_user
from api_server.server import app
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)
from tests.test_template import TestTemplate


def _auth_with_payments():
    return AuthenticatedUser(
        user_id="test-user",
        auth_method="api_key",
        scopes=["payments:read", "payments:write"],
    )


def _auth_read_only():
    return AuthenticatedUser(
        user_id="test-user",
        auth_method="api_key",
        scopes=["payments:read"],
    )


class TestAgenticPaymentsAPI(TestTemplate):
    def setup_method(self):
        PaymentRegistry.reset()
        app.dependency_overrides[get_authenticated_user] = _auth_with_payments
        self.client = TestClient(app, raise_server_exceptions=False)

    def teardown_method(self):
        PaymentRegistry.reset()
        app.dependency_overrides.clear()

    def _setup_registry_with_mock(self, mock_proto):
        """Insert a mock protocol into the registry."""
        registry = PaymentRegistry.get()
        registry._protocols["x402"] = mock_proto
        registry._initialized = True

    # --- /status ---

    def test_status_returns_empty_when_no_protocols(self):
        resp = self.client.get("/api/v1/agentic-payments/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["protocols"] == []

    def test_status_shows_registered_protocol(self):
        mock_proto = MagicMock()
        mock_proto.is_available = True
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.get("/api/v1/agentic-payments/status")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["protocols"]) == 1
        assert data["protocols"][0]["name"] == "x402"
        assert data["protocols"][0]["available"] is True

    # --- /requirements ---

    def test_requirements_unknown_protocol_returns_400(self):
        # Ensure registry is initialized but empty
        registry = PaymentRegistry.get()
        registry._initialized = True

        resp = self.client.post(
            "/api/v1/agentic-payments/requirements",
            json={"protocol": "nonexistent", "amount": "0.01"},
        )
        assert resp.status_code == 400

    def test_requirements_unavailable_protocol_returns_503(self):
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=False)
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/requirements",
            json={"protocol": "x402", "amount": "0.01"},
        )
        assert resp.status_code == 503

    def test_requirements_success(self):
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.build_payment_requirement = AsyncMock(
            return_value=PaymentRequirement(
                protocol=PaymentProtocolName.X402,
                network="base-sepolia",
                asset="USDC",
                amount="0.01",
                recipient="0xwallet",
                facilitator_url="https://x402.org/facilitator",
                description="test",
            )
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/requirements",
            json={"protocol": "x402", "amount": "0.01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["protocol"] == "x402"
        assert data["amount"] == "0.01"
        assert data["network"] == "base-sepolia"

    def test_requirements_protocol_exception_returns_500(self):
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.build_payment_requirement = AsyncMock(
            side_effect=RuntimeError("SDK exploded")
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/requirements",
            json={"protocol": "x402", "amount": "0.01"},
        )
        assert resp.status_code == 500

    # --- /verify ---

    def test_verify_unknown_protocol_returns_400(self):
        registry = PaymentRegistry.get()
        registry._initialized = True

        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "nonexistent",
                "payload": {},
                "requirement": {"network": "base-sepolia"},
            },
        )
        assert resp.status_code == 400

    def test_verify_completed_payment(self):
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.verify_payment = AsyncMock(
            return_value=PaymentResult(
                status=PaymentStatus.COMPLETED,
                protocol=PaymentProtocolName.X402,
                transaction_id="0xtx123",
            )
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "x402",
                "payload": {"sig": "abc"},
                "requirement": {
                    "network": "base-sepolia",
                    "asset": "USDC",
                    "amount": "0.001",
                    "recipient": "0xrecipient",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["transaction_id"] == "0xtx123"
        assert data["error"] is None

    def test_verify_rejected_payment(self):
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.verify_payment = AsyncMock(
            return_value=PaymentResult(
                status=PaymentStatus.REJECTED,
                protocol=PaymentProtocolName.X402,
                error="ERR_INSUFFICIENT_BALANCE",
            )
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "x402",
                "payload": {"sig": "abc"},
                "requirement": {
                    "network": "base-sepolia",
                    "asset": "USDC",
                    "amount": "0.001",
                    "recipient": "0xrecipient",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["error"] == "ERR_INSUFFICIENT_BALANCE"

    def test_verify_failed_payment_returns_502(self):
        """FAILED status (infrastructure error) -> 502 not 200."""
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.verify_payment = AsyncMock(
            return_value=PaymentResult(
                status=PaymentStatus.FAILED,
                protocol=PaymentProtocolName.X402,
                error="Facilitator timeout",
            )
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "x402",
                "payload": {},
                "requirement": {"network": "base-sepolia"},
            },
        )
        assert resp.status_code == 502

    def test_verify_exception_returns_500(self):
        """Protocol raises unexpected exception -> 500."""
        mock_proto = MagicMock()
        mock_proto.initialize = AsyncMock(return_value=True)
        mock_proto.verify_payment = AsyncMock(
            side_effect=RuntimeError("Network timeout")
        )
        self._setup_registry_with_mock(mock_proto)

        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "x402",
                "payload": {},
                "requirement": {"network": "base-sepolia"},
            },
        )
        assert resp.status_code == 500

    def test_verify_missing_payload_returns_422(self):
        """Missing required field -> 422 validation error."""
        resp = self.client.post(
            "/api/v1/agentic-payments/verify",
            json={"protocol": "x402"},
        )
        assert resp.status_code == 422

    # --- Auth / Scopes ---

    def test_status_requires_payments_read_scope(self):
        app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
            user_id="no-scope",
            auth_method="api_key",
            scopes=["services:read"],
        )
        client = TestClient(app)
        resp = client.get("/api/v1/agentic-payments/status")
        assert resp.status_code == 403

    def test_requirements_requires_payments_write_scope(self):
        app.dependency_overrides[get_authenticated_user] = _auth_read_only
        client = TestClient(app)
        resp = client.post(
            "/api/v1/agentic-payments/requirements",
            json={"protocol": "x402", "amount": "0.01"},
        )
        assert resp.status_code == 403

    def test_verify_requires_payments_write_scope(self):
        app.dependency_overrides[get_authenticated_user] = _auth_read_only
        client = TestClient(app)
        resp = client.post(
            "/api/v1/agentic-payments/verify",
            json={
                "protocol": "x402",
                "payload": {},
                "requirement": {"network": "base-sepolia"},
            },
        )
        assert resp.status_code == 403

    # --- Route registration ---

    def test_agentic_payment_routes_registered(self):
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/agentic-payments/status" in routes
        assert "/api/v1/agentic-payments/requirements" in routes
        assert "/api/v1/agentic-payments/verify" in routes
