"""Persistent CLI state stored as JSON in ~/.config/<package>/state.json."""

import json
from pathlib import Path
from typing import Any

_PACKAGE_NAME = "daysurface"
_CONFIG_DIR = Path.home() / ".config" / _PACKAGE_NAME
_STATE_FILE = _CONFIG_DIR / "state.json"

# State written before the DaySurface rename. Read-only fallback: this file
# holds the telemetry opt-out and the one-time notice flags, so ignoring it
# would silently re-enable telemetry for someone who had opted out. The next
# save_state writes to the new location, retiring the old file.
_LEGACY_STATE_FILES = (Path.home() / ".config" / "mcp-template" / "state.json",)


def load_state() -> dict[str, Any]:
    """Read the persisted state dict, returning {} on missing or corrupt files."""
    for path in (_STATE_FILE, *_LEGACY_STATE_FILES):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Write the state dict to disk."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass
