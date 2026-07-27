"""Tests for output formatter."""

from src.cli.state import OutputFormat, output_format
from src.utils.output import render
from tests.test_template import TestTemplate


class TestRender(TestTemplate):
    def test_render_dict_as_table(self):
        token = output_format.set(OutputFormat.TABLE)
        try:
            render({"key": "value"}, title="Test")
        finally:
            output_format.reset(token)

    def test_render_dict_as_json(self):
        token = output_format.set(OutputFormat.JSON)
        try:
            render({"key": "value"})
        finally:
            output_format.reset(token)

    def test_render_dict_as_plain(self):
        token = output_format.set(OutputFormat.PLAIN)
        try:
            render({"key": "value"}, title="Test")
        finally:
            output_format.reset(token)

    def test_render_list_of_dicts_as_table(self):
        token = output_format.set(OutputFormat.TABLE)
        try:
            render([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        finally:
            output_format.reset(token)

    def test_render_plain_string(self):
        token = output_format.set(OutputFormat.PLAIN)
        try:
            render("hello")
        finally:
            output_format.reset(token)
