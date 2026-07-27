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

    def test_config_get_nonexistent(self):
        with pytest.raises(KeyError):
            config_get(ConfigGetInput(key="nonexistent.key"))


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
