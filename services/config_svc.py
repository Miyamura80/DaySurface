"""Config service - pure business logic."""

from pathlib import Path

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
    admin_scope="admin:read",
)
def config_show(input: ConfigShowInput) -> ConfigShowResult:
    return ConfigShowResult(config=global_config.to_dict())


@service(
    name="config_get",
    description="Get a single configuration value by dot-separated key",
    input_model=ConfigGetInput,
    output_model=ConfigGetResult,
    admin_scope="admin:read",
)
def config_get(input: ConfigGetInput) -> ConfigGetResult:
    obj = global_config
    for part in input.key.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            if isinstance(obj, dict):
                try:
                    obj = obj[part]
                except KeyError:
                    raise KeyError(f"Key not found: {input.key}") from None
            else:
                raise KeyError(f"Key not found: {input.key}") from None

    if hasattr(obj, "model_dump"):
        return ConfigGetResult(key=input.key, value=obj.model_dump())
    return ConfigGetResult(key=input.key, value=obj)


@service(
    name="config_set",
    description="Set a configuration override",
    input_model=ConfigSetInput,
    output_model=ConfigSetResult,
    mutating=True,
    admin_scope="admin:write",
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
