"""Tests for agentic payments configuration models."""

from common import global_config
from common.config_models import (
    AcpProtocolConfig,
    AgenticPaymentsConfig,
    MppProtocolConfig,
    X402ProtocolConfig,
)
from tests.test_template import TestTemplate


class TestAgenticPaymentsConfig(TestTemplate):
    def test_default_all_disabled(self):
        cfg = AgenticPaymentsConfig()
        assert cfg.x402.enabled is False
        assert cfg.mpp.enabled is False
        assert cfg.acp.enabled is False

    def test_x402_defaults(self):
        cfg = X402ProtocolConfig()
        assert cfg.facilitator_url == "https://x402.org/facilitator"
        assert cfg.network == "base-sepolia"
        assert cfg.default_asset == "USDC"
        assert cfg.testnet is True

    def test_x402_secrets_are_env_var_names(self):
        cfg = X402ProtocolConfig()
        assert cfg.wallet_address_env == "X402_WALLET_ADDRESS"
        assert cfg.private_key_env == "X402_PRIVATE_KEY"
        assert not cfg.wallet_address_env.startswith("0x")

    def test_mpp_stub(self):
        cfg = MppProtocolConfig()
        assert cfg.enabled is False

    def test_acp_stub(self):
        cfg = AcpProtocolConfig()
        assert cfg.enabled is False

    def test_config_loads_from_global(self):
        assert hasattr(global_config, "payments")
        assert isinstance(global_config.payments, AgenticPaymentsConfig)
