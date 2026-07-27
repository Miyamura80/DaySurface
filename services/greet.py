"""Greet service - pure business logic."""

from models.greet import GreetInput, GreetResult
from services import service


@service(
    name="greet",
    description="Greet a user by name",
    input_model=GreetInput,
    output_model=GreetResult,
)
def greet(input: GreetInput) -> GreetResult:
    message = f"Hello, {input.name}!"
    if input.shout:
        message = message.upper()
    return GreetResult(message=message, times=input.times)
