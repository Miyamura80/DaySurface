"""CLI theme and branding utilities."""

from pathlib import Path

import yaml
from rich.console import Console
from rich.theme import Theme

_ROOT = Path(__file__).parent.parent.parent


def _load_cli_config() -> dict[str, object]:
    """Read the cli section from global_config.yaml without triggering full config init."""
    try:
        config_path = _ROOT / "common" / "global_config.yaml"
        data = yaml.safe_load(config_path.read_text()) or {}
        return data.get("cli", {})
    except (OSError, yaml.YAMLError, AttributeError):
        # AttributeError covers the case where the YAML root parses to a
        # non-dict scalar/list, making the subsequent `.get("cli", {})` fail.
        return {}


def get_cli_emoji() -> str:
    """Return the configured CLI emoji, or empty string if none set."""
    value = _load_cli_config().get("emoji", "")
    return str(value) if value else ""


def get_primary_color() -> str:
    """Return the configured primary color for Rich markup."""
    value = _load_cli_config().get("primary_color", "cyan")
    return str(value) if value else "cyan"


def get_secondary_color() -> str:
    """Return the configured secondary color for Rich markup."""
    value = _load_cli_config().get("secondary_color", "green")
    return str(value) if value else "green"


def make_theme() -> Theme:
    """Build a Rich Theme from the configured branding colors."""
    primary = get_primary_color()
    secondary = get_secondary_color()
    return Theme(
        {
            "primary": primary,
            "secondary": secondary,
        }
    )


def make_console() -> Console:
    """Return a Rich Console pre-loaded with the project color theme."""
    return Console(theme=make_theme())
