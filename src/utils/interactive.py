"""Interactive fallback decorator for missing CLI arguments."""

import inspect
import sys
import types
from functools import wraps
from typing import Any, get_type_hints

import questionary


def _resolve_hint(hint: Any) -> Any:
    """Unwrap Optional[X] to X and return the base type hint."""
    origin = getattr(hint, "__origin__", None)
    if origin is types.UnionType:
        hint_args = getattr(hint, "__args__", ())
        non_none = [a for a in hint_args if a is not type(None)]
        return non_none[0] if non_none else str
    return hint


def _prompt_for_value(name: str, hint: Any, default: Any) -> Any:
    """Prompt the user for a value based on its type hint."""
    label = name.replace("_", " ")
    if hint is bool:
        return questionary.confirm(f"Enter {label}:", default=False).ask()
    if hint is int:
        raw = questionary.text(f"Enter {label}:").ask()
        return int(raw) if raw else default
    return questionary.text(f"Enter {label}:").ask()


def interactive_fallback(func: Any) -> Any:
    """When required args are None, prompt the user interactively.

    Uses inspect.signature to detect missing params and get_type_hints
    for prompt type (bool -> confirm, str -> text).
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not sys.stdin.isatty():
            return func(*args, **kwargs)

        sig = inspect.signature(func)
        try:
            hints = get_type_hints(func)
        except (NameError, TypeError, AttributeError):
            # Forward refs that don't resolve in this scope, or unhashable defaults;
            # fall back to no hints rather than failing the prompt.
            hints = {}

        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()

        for name, param in sig.parameters.items():
            if name in bound.arguments and bound.arguments[name] is not None:
                continue
            if (
                param.default is not inspect.Parameter.empty
                and param.default is not None
            ):
                continue

            hint = _resolve_hint(hints.get(name, str))
            value = _prompt_for_value(name, hint, param.default)

            if value is None:
                return None
            bound.arguments[name] = value

        return func(*bound.args, **bound.kwargs)

    return wrapper
