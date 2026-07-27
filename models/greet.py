"""Models for the greet service."""

from pydantic import BaseModel


class GreetInput(BaseModel):
    name: str
    shout: bool = False
    times: int = 1


class GreetResult(BaseModel):
    message: str
    times: int = 1
