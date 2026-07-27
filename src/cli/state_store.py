"""Persistent CLI state stored as JSON in ~/.config/<package>/state.json."""

import json
from pathlib import Path
from typing import Any

_PACKAGE_NAME = "mcp-template"
_CONFIG_DIR = Path.home() / ".config" / _PACKAGE_NAME
_STATE_FILE = _CONFIG_DIR / "state.json"


def load_state() -> dict[str, Any]:
    """Read the persisted state dict, returning {} on missing or corrupt files."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Write the state dict to disk."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass
