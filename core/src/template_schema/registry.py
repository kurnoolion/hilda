"""Extensibility registries for FR-7, NFR-14.

Mutable sets seeded from enum values; extended via config at startup.
Validators check against the registry, not the closed enum.
"""
from __future__ import annotations

from core.src.template_schema.enums import (
    CustomerDeliveryModality,
    DeliveryState,
    ItemType,
    TrackingModality,
)

DeliveryStateRegistry: set[str] = {e.value for e in DeliveryState}
ItemTypeRegistry: set[str] = {e.value for e in ItemType}
TrackingModalityRegistry: set[str] = {e.value for e in TrackingModality}
CustomerDeliveryModalityRegistry: set[str] = {e.value for e in CustomerDeliveryModality}


def extend_registry(registry: set[str], values: list[str]) -> None:
    """Idempotent — duplicates silently ignored."""
    registry.update(values)


def validate_in_registry(
    registry: set[str], value: str, *, registry_name: str
) -> str:
    if value not in registry:
        raise ValueError(
            f"value {value!r} not in {registry_name} registry "
            f"(known: {sorted(registry)})"
        )
    return value
