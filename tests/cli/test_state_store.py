"""Tests for persisted CLI state, including the pre-rename fallback."""

import json
from unittest.mock import patch

from src.cli.state_store import load_state, save_state
from tests.test_template import TestTemplate


class TestStateStore(TestTemplate):
    def test_load_missing_returns_empty(self, tmp_path):
        with (
            patch("src.cli.state_store._STATE_FILE", tmp_path / "state.json"),
            patch("src.cli.state_store._LEGACY_STATE_FILES", ()),
        ):
            assert load_state() == {}

    def test_round_trip(self, tmp_path):
        state_file = tmp_path / "state.json"
        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._CONFIG_DIR", tmp_path),
            patch("src.cli.state_store._LEGACY_STATE_FILES", ()),
        ):
            save_state({"telemetry_disabled": True})
            assert load_state() == {"telemetry_disabled": True}

    def test_load_corrupt_returns_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{not json")
        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._LEGACY_STATE_FILES", ()),
        ):
            assert load_state() == {}

    def test_falls_back_to_legacy_state_file(self, tmp_path):
        # The pre-rename state file holds the telemetry opt-out. Ignoring it
        # would silently re-enable telemetry for someone who had opted out.
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"telemetry_disabled": True}))
        with (
            patch("src.cli.state_store._STATE_FILE", tmp_path / "state.json"),
            patch("src.cli.state_store._LEGACY_STATE_FILES", (legacy,)),
        ):
            assert load_state() == {"telemetry_disabled": True}

    def test_current_state_file_wins_over_legacy(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"telemetry_disabled": False}))
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"telemetry_disabled": True}))
        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._LEGACY_STATE_FILES", (legacy,)),
        ):
            assert load_state() == {"telemetry_disabled": False}

    def test_save_writes_to_current_location_only(self, tmp_path):
        state_file = tmp_path / "state.json"
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"telemetry_disabled": True}))
        with (
            patch("src.cli.state_store._STATE_FILE", state_file),
            patch("src.cli.state_store._CONFIG_DIR", tmp_path),
            patch("src.cli.state_store._LEGACY_STATE_FILES", (legacy,)),
        ):
            save_state({"telemetry_disabled": False})
            assert state_file.exists()
            assert json.loads(legacy.read_text()) == {"telemetry_disabled": True}
