"""Tests for service layer - pure business logic, no transport."""

import pytest

from models.config import ConfigGetInput, ConfigShowInput
from models.doctor import DoctorInput
from models.greet import GreetInput
from services.config_svc import config_get, config_show
from services.doctor_svc import doctor
from services.greet import greet
from tests.test_template import TestTemplate


class TestGreetService(TestTemplate):
    def test_greet_basic(self):
        result = greet(GreetInput(name="Alice"))
        assert result.message == "Hello, Alice!"

    def test_greet_shout(self):
        result = greet(GreetInput(name="Alice", shout=True))
        assert result.message == "HELLO, ALICE!"

    def test_greet_times(self):
        result = greet(GreetInput(name="Bob", times=3))
        assert result.message == "Hello, Bob!"
        assert result.times == 3


class TestConfigService(TestTemplate):
    def test_config_show(self):
        result = config_show(ConfigShowInput())
        assert isinstance(result.config, dict)
        assert len(result.config) > 0

    def test_config_get(self):
        result = config_get(ConfigGetInput(key="llm_config.cache_enabled"))
        assert result.key == "llm_config.cache_enabled"
        assert result.value is False

    def test_config_get_nonexistent(self):
        with pytest.raises(KeyError):
            config_get(ConfigGetInput(key="nonexistent.key"))

    def test_config_show_excludes_env_secrets(self):
        # config_show must never expose environment-sourced secrets: they live
        # in a different settings source than the YAML config and must stay out
        # of any transport-reachable dump. Guards against the secret-disclosure
        # regression where config_show returned the full env-inclusive dump.
        result = config_show(ConfigShowInput())
        for secret_field in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "BACKEND_DB_URI",
            "GOOGLE_TOKEN_ENC_KEY",
            "SESSION_SECRET_KEY",
            "STRIPE_SECRET_KEY",
            "WORKOS_API_KEY",
            "X402_PRIVATE_KEY",
        ):
            assert secret_field not in result.config

    def test_config_get_rejects_env_secrets(self):
        # A secret key is unreachable through config_get - it raises KeyError
        # rather than returning the value.
        for secret_field in (
            "OPENAI_API_KEY",
            "BACKEND_DB_URI",
            "GOOGLE_TOKEN_ENC_KEY",
        ):
            with pytest.raises(KeyError):
                config_get(ConfigGetInput(key=secret_field))


class TestDoctorService(TestTemplate):
    def test_doctor_runs(self):
        result = doctor(DoctorInput())
        assert len(result.checks) > 0
        assert isinstance(result.has_failures, bool)

    def test_doctor_check_names(self):
        result = doctor(DoctorInput())
        names = [c.name for c in result.checks]
        assert "Python version" in names
        assert "uv installed" in names
