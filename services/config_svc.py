"""Config service - pure business logic."""

from pathlib import Path
from typing import Any

import yaml

from common import global_config
from models.config import (
    ConfigGetInput,
    ConfigGetResult,
    ConfigSetInput,
    ConfigSetResult,
    ConfigShowInput,
    ConfigShowResult,
)
from services import service

_ROOT_DIR = Path(__file__).parent.parent


def _coerce_value(value: str) -> bool | int | float | str | None:
    """Attempt to coerce a string value to bool/int/float."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


@service(
    name="config_show",
    description="Show the full project configuration",
    input_model=ConfigShowInput,
    output_model=ConfigShowResult,
)
def config_show(input: ConfigShowInput) -> ConfigShowResult:
    # YAML-only view: never expose environment-sourced secrets (API keys,
    # BACKEND_DB_URI, GOOGLE_TOKEN_ENC_KEY, SESSION_SECRET_KEY, ...). This
    # service is reachable over the authenticated HTTP transport.
    return ConfigShowResult(config=global_config.to_yaml_dict())


@service(
    name="config_get",
    description="Get a single configuration value by dot-separated key",
    input_model=ConfigGetInput,
    output_model=ConfigGetResult,
)
def config_get(input: ConfigGetInput) -> ConfigGetResult:
    # Walk the YAML-only view so environment-sourced secrets are unreachable:
    # config_get("OPENAI_API_KEY") / config_get("BACKEND_DB_URI") now raise
    # KeyError instead of returning the secret.
    obj: Any = global_config.to_yaml_dict()
    for part in input.key.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            raise KeyError(f"Key not found: {input.key}")
    return ConfigGetResult(key=input.key, value=obj)


@service(
    name="config_set",
    description="Set a configuration override",
    input_model=ConfigSetInput,
    output_model=ConfigSetResult,
    mutating=True,
)
def config_set(input: ConfigSetInput) -> ConfigSetResult:
    override_path = _ROOT_DIR / ".global_config.yaml"

    existing: dict = {}
    if override_path.exists():
        with open(override_path) as f:
            existing = yaml.safe_load(f) or {}

    parts = input.key.split(".")
    current = existing
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    coerced = _coerce_value(input.value)
    current[parts[-1]] = coerced

    with open(override_path, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False)

    return ConfigSetResult(key=input.key, coerced_value=coerced)
