"""Patch-field semantics: distinguish "omitted" from "explicit null".

A patch/update tool needs three distinct states per field: *omitted* (leave the
stored value untouched), *null* (clear it), and *a value* (overwrite). This
module provides the ``UNSET`` sentinel that encodes "omitted" in a way that
survives the MCP transport, plus small helpers for consuming and serializing it.

It is deliberately transport- and feature-agnostic (no Gmail imports) so any
patch-style input model can reuse it.
"""

from typing import Annotated

from pydantic import PlainSerializer


class _UnsetType:
    """Sentinel that survives the MCP transport to mean "field was omitted".

    ``model_fields_set`` cannot supply this over MCP: FastMCP materializes every
    declared parameter to its default before invoking the tool
    (``func_metadata.model_dump_one_level`` calls ``getattr`` for every field),
    so an omitted field arrives as its default and lands in ``model_fields_set``
    indistinguishably from one the caller passed. With a default of ``None``
    that collapses omitted into null and silently clears fields the caller never
    mentioned.

    A dedicated sentinel default survives that round-trip: omitted -> ``UNSET``
    (preserve), ``null`` -> ``None`` (clear), value -> the value. Under
    ``arbitrary_types_allowed`` Pydantic validates it by identity (no coercion)
    and contributes no JSON schema for it, so the wire contract for these fields
    stays ``string | null`` - the sentinel never leaks into the advertised input
    schema.
    """

    _instance: "_UnsetType | None" = None

    def __new__(cls) -> "_UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _UnsetType()


def unset_to[T](value: T | _UnsetType, fallback: T) -> T:
    """Resolve a patch field: ``fallback`` when omitted (``UNSET``), else ``value``.

    ``value`` may itself be ``None`` (an explicit "clear"), which is returned
    verbatim - only the ``UNSET`` sentinel selects the fallback.
    """
    if isinstance(value, _UnsetType):
        return fallback
    return value


# UNSET is not JSON-serializable on its own, so a model carrying it would raise
# in ``model_dump(mode="json")`` (e.g. if a patch model were ever logged or run
# through the idempotency store). Collapse the sentinel to ``null`` on the wire
# - an omitted field and an explicit-null field dump identically, which is
# correct: both mean "no value here". Validation still distinguishes them via
# the sentinel; only the serialized form collapses. ``when_used="json"`` keeps
# Python-mode dumps (``model_copy`` etc.) identity-preserving.
_UnsetJson = PlainSerializer(
    lambda v: None if isinstance(v, _UnsetType) else v, when_used="json"
)

# A patchable string field: string-or-null on the wire, UNSET-aware in Python.
# A plain alias (not a PEP 695 ``type`` statement) so Pydantic inlines the
# anyOf into each field instead of emitting a ``$ref`` to a named ``$def``.
_PatchStr = Annotated[str | None | _UnsetType, _UnsetJson]
