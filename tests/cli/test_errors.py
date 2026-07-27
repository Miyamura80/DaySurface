"""Tests for error handling."""

import sys

from src.utils.errors import _original_excepthook, install_error_handler
from tests.test_template import TestTemplate


class TestErrorHandler(TestTemplate):
    def test_install_friendly_handler(self):
        install_error_handler(debug=False)
        assert sys.excepthook is not _original_excepthook
        # Restore
        sys.excepthook = _original_excepthook

    def test_install_debug_handler(self):
        install_error_handler(debug=True)
        assert sys.excepthook is not _original_excepthook
        # Restore
        sys.excepthook = _original_excepthook
