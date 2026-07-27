"""Ports connecting the PDF core to external sources and destinations.

The PDF core never imports Gmail (or any provider) modules. Instead it
declares two ports here - source resolution (``pdf_open``) and export
delivery (``pdf_export``) - and adapters register themselves per ``type``
discriminator at import time. ``services/pdf_gmail_bridge.py`` is the only
module wiring the Gmail side in; extracting the PDF domain as a standalone
add-on later means swapping that one adapter module.

``services.discover_services()`` imports every ``services.*`` module, so all
in-repo adapters are registered before any transport serves a call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from models.pdf_forms import PdfDelivery


@dataclass(frozen=True)
class ResolvedPdfSource:
    """A source locator resolved to actual bytes."""

    filename: str
    data: bytes


# Adapters receive (user_id, source_model) and return the fetched bytes.
SourceResolver = Callable[[str, Any], ResolvedPdfSource]
# Adapters receive (user_id, destination_model, filename, data) and report
# back what was delivered where (destination-neutral shape).
DestinationHandler = Callable[[str, Any, str, bytes], PdfDelivery]

_source_resolvers: dict[str, SourceResolver] = {}
_destination_handlers: dict[str, DestinationHandler] = {}


class PdfPortNotRegisteredError(Exception):
    """Raised when no adapter is registered for a source/destination type."""


def register_source_resolver(source_type: str, fn: SourceResolver) -> None:
    if source_type in _source_resolvers:
        raise ValueError(f"Duplicate source resolver for {source_type!r}")
    _source_resolvers[source_type] = fn


def register_destination_handler(destination_type: str, fn: DestinationHandler) -> None:
    if destination_type in _destination_handlers:
        raise ValueError(f"Duplicate destination handler for {destination_type!r}")
    _destination_handlers[destination_type] = fn


def resolve_source(user_id: str, source: Any) -> ResolvedPdfSource:
    resolver = _source_resolvers.get(source.type)
    if resolver is None:
        raise PdfPortNotRegisteredError(
            f"No source resolver registered for type {source.type!r}."
        )
    return resolver(user_id, source)


def deliver_to_destination(
    user_id: str, destination: Any, filename: str, data: bytes
) -> PdfDelivery:
    handler = _destination_handlers.get(destination.type)
    if handler is None:
        raise PdfPortNotRegisteredError(
            f"No destination handler registered for type {destination.type!r}."
        )
    return handler(user_id, destination, filename, data)
