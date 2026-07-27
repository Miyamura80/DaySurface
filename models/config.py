"""Models for the config service."""

from typing import Any

from pydantic import BaseModel


class ConfigShowInput(BaseModel):
    pass


class ConfigShowResult(BaseModel):
    config: dict[str, Any]


class ConfigGetInput(BaseModel):
    key: str


class ConfigGetResult(BaseModel):
    key: str
    value: Any


class ConfigSetInput(BaseModel):
    key: str
    value: str


class ConfigSetResult(BaseModel):
    key: str
    coerced_value: Any
