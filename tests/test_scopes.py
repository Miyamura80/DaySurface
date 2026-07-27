"""Tests for API key scopes: check_scopes, validate_scopes, and require_scopes."""

import pytest

from api_server.auth.scopes import (
    ALL_SCOPES,
    SCOPE_TEMPLATES,
    check_scopes,
    validate_scopes,
)
from tests.test_template import TestTemplate


class TestScopes(TestTemplate):
    # --- validate_scopes ---

    def test_validate_known_scopes(self):
        result = validate_scopes(["services:read", "services:execute"])
        assert "services:read" in result
        assert "services:execute" in result

    def test_validate_template_expansion(self):
        result = validate_scopes(["standard"])
        assert "services:read" in result
        assert "services:execute" in result

    def test_validate_admin_template(self):
        result = validate_scopes(["admin"])
        assert "*" in result

    def test_validate_wildcard(self):
        result = validate_scopes(["*"])
        assert result == ["*"]

    def test_validate_resource_wildcard(self):
        result = validate_scopes(["services:*"])
        assert "services:*" in result

    def test_validate_unknown_scope_raises(self):
        with pytest.raises(ValueError, match="Unknown scope"):
            validate_scopes(["bogus:scope"])

    # --- check_scopes ---

    def test_check_none_grants_all(self):
        """None scopes (legacy key) should pass any check."""
        assert check_scopes(["services:execute", "admin:write"], None) is True

    def test_check_wildcard_grants_all(self):
        assert check_scopes(["services:execute", "admin:write"], ["*"]) is True

    def test_check_exact_match(self):
        assert check_scopes(["services:read"], ["services:read"]) is True

    def test_check_missing_scope(self):
        assert check_scopes(["services:execute"], ["services:read"]) is False

    def test_check_resource_wildcard(self):
        assert check_scopes(["services:execute"], ["services:*"]) is True

    def test_check_resource_wildcard_wrong_resource(self):
        assert check_scopes(["admin:write"], ["services:*"]) is False

    def test_check_multiple_required(self):
        assert (
            check_scopes(
                ["services:read", "services:execute"],
                ["services:read", "services:execute"],
            )
            is True
        )

    def test_check_partial_match_fails(self):
        assert (
            check_scopes(
                ["services:read", "admin:write"],
                ["services:read"],
            )
            is False
        )

    # --- constants ---

    def test_all_scopes_not_empty(self):
        assert len(ALL_SCOPES) >= 4

    def test_templates_contain_expected(self):
        assert "read_only" in SCOPE_TEMPLATES
        assert "standard" in SCOPE_TEMPLATES
        assert "admin" in SCOPE_TEMPLATES
