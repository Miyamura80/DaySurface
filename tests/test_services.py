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
        # Recursive walk so a secret nested at any depth is caught, not just
        # top-level keys, and enforce the invariant for *every* declared secret
        # field name rather than a hand-picked subset.
        from common.global_config import _SECRET_FIELD_NAMES  # noqa: PLC0415

        result = config_show(ConfigShowInput())

        def _keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    yield from _keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _keys(v)

        present = set(_keys(result.config))
        assert not (present & _SECRET_FIELD_NAMES), present & _SECRET_FIELD_NAMES

    def test_config_show_drops_secret_named_yaml_keys(self):
        # Defense in depth: a secret-named key hand-placed in a YAML file (e.g.
        # .global_config.yaml) must be dropped from the view, not echoed back.
        import sys  # noqa: PLC0415

        # The module (not the singleton, which shadows it via common/__init__).
        gc = sys.modules["common.global_config"]
        original = gc._yaml_config_cache
        gc._yaml_config_cache = {  # ty: ignore[unresolved-attribute]
            **original,
            "OPENAI_API_KEY": "leaked",
            "openai_api_key": "leaked-lowercase",  # pydantic is case-insensitive
            "nested": {"SESSION_SECRET_KEY": "leaked", "keep": 1},
        }
        try:
            cfg = config_show(ConfigShowInput()).config
        finally:
            gc._yaml_config_cache = original  # ty: ignore[unresolved-attribute]
        assert "OPENAI_API_KEY" not in cfg
        assert "openai_api_key" not in cfg  # lowercase secret dropped too
        assert "SESSION_SECRET_KEY" not in cfg["nested"]
        assert cfg["nested"]["keep"] == 1  # non-secret siblings survive

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
