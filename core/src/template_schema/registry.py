"""Extensibility registries for FR-7, NFR-14.

Mutable sets seeded from enum values; extended via config at startup.
Validators check against the registry, not the closed enum.

Enum-seeded registries (most common):
  DeliveryStateRegistry, ItemTypeRegistry, TrackingModalityRegistry,
  CustomerDeliveryModalityRegistry, DocTypeRegistry, RuleActionRegistry,
  RuleTriggerRegistry.

Customer-extensible-from-empty registry:
  TGNameRegistry — TG names are per-customer (e.g. "Hardware", "Software");
    no canonical enum exists. Populated by config loader from
    customizations/template_schemas/<customer>/tg_groups.yaml at startup.
"""
from __future__ import annotations

from core.src.template_schema.enums import (
    CustomerDeliveryModality,
    DeliveryState,
    DocType,
    ItemType,
    RuleActionType,
    RuleTriggerType,
    TrackingModality,
)

# Enum-seeded registries — initial value set matches the closed Python enum.
# Extended at startup via config; runtime validators check the registry, not the enum.
DeliveryStateRegistry: set[str]            = {e.value for e in DeliveryState}
ItemTypeRegistry: set[str]                 = {e.value for e in ItemType}
TrackingModalityRegistry: set[str]         = {e.value for e in TrackingModality}
CustomerDeliveryModalityRegistry: set[str] = {e.value for e in CustomerDeliveryModality}
DocTypeRegistry: set[str]                  = {e.value for e in DocType}
RuleActionRegistry: set[str]               = {e.value for e in RuleActionType}
RuleTriggerRegistry: set[str]              = {e.value for e in RuleTriggerType}

# Customer-extensible-from-empty registry — no canonical enum.
# Populated at startup by config loader reading per-customer TG name lists.
TGNameRegistry: set[str] = set()


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
