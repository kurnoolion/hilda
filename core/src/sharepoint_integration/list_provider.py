"""SharePointListProvider Protocol + FileBasedListProvider boilerplate.

Pure lookup service — no HTTP, no side effects. customizations/ may provide
non-file-based implementations (e.g. DB-backed). Anchors [D-020].

3-entity canonical set per architect Q1 lock 2026-06-25 + [D-104]:
`delivery_items`, `milestones`, `projects`. The Protocol itself accepts any
entity string (open to future extension), but `FileBasedListProvider` rejects
the legacy 8-list entities (`customers`, `devices`, `users`, `pm_credentials`,
`communication_log`, `tg_groups`) at config-load time per architect Q1 + Q3
locks — those are SP UI engineer's display surface (or Postgres-internal per
FR-42) and NOT in HILDA's SP read/write scope.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.config import ListScope

# Canonical 3-entity set per architect Q1 lock 2026-06-25 + [D-104].
# Per-customer SP list naming pattern `<base>_<customer_id>` (e.g.
# `Deliverables_<customer_id>`, `Milestones_<customer_id>`, `Projects_<customer_id>`).
_CANONICAL_ENTITIES: frozenset[str] = frozenset(
    {"delivery_items", "milestones", "projects"}
)

# Legacy entities removed by architect Q1 contraction. Surface SHP-E002 with a
# clear-enough context if a YAML still references them, rather than silently
# loading a stale config.
_LEGACY_ENTITIES: frozenset[str] = frozenset(
    {
        "customers",
        "devices",
        "users",
        "pm_credentials",
        "communication_log",
        "tg_groups",
    }
)


class SharePointListProvider(Protocol):
    """Maps (entity, scope) → SP list name + canonical→SP-column mapping."""

    def get_list_name(self, entity: str, scope: ListScope) -> str:
        ...

    def get_column_map(self, entity: str, scope: ListScope) -> dict[str, str]:
        ...

    def to_sp_fields(
        self,
        entity: str,
        scope: ListScope,
        canonical: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def from_sp_fields(
        self,
        entity: str,
        scope: ListScope,
        sp_fields: dict[str, Any],
    ) -> dict[str, Any]:
        ...


# --- FileBasedListProvider --------------------------------------------------


class _ListEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    columns: dict[str, str] = {}


class _CustomerListConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str  # renamed from customer_slug per [D-091]
    lists: dict[str, _ListEntry]


class _DeviceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Device-level overrides are Ph-2/Ph-3+ Deferred per architect Q1 2026-06-25;
    # the model is retained for forward-compat YAML schema but not exercised in
    # Ph-1.  Renamed slug→id per [D-091].
    customer_id: str
    device_id: str
    entity: str
    list_name: str | None = None
    columns: dict[str, str] = {}


class _DeviceOverridesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_overrides: list[_DeviceOverride] = []


class FileBasedListProvider:
    """Reads YAML from customizations/sharepoint_config/.

    Layout:
      customizations/sharepoint_config/customers/<slug>.yaml
      customizations/sharepoint_config/devices/special_devices.yaml
    """

    def __init__(
        self,
        config_base: Path = Path("customizations/sharepoint_config"),
    ) -> None:
        self.config_base = Path(config_base)
        self._customers: dict[str, _CustomerListConfig] = {}
        self._device_overrides: list[_DeviceOverride] = []
        self._load()

    def _load(self) -> None:
        customers_dir = self.config_base / "customers"
        if customers_dir.exists():
            for path in sorted(customers_dir.glob("*.yaml")):
                with path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                cfg = _CustomerListConfig.model_validate(data)
                # Validate against the canonical 3-entity set per architect Q1
                # lock 2026-06-25 + [D-104]. Legacy 8-list entities are
                # explicitly rejected with SHP-E002; unknown entities also
                # raise SHP-E002 so a typo doesn't silently load.
                for entity in cfg.lists:
                    if entity in _LEGACY_ENTITIES or entity not in _CANONICAL_ENTITIES:
                        raise PipelineError(
                            "SHP-E002",
                            context={
                                "entity": entity,
                                "customer": cfg.customer_id,
                                "device": "",
                            },
                        )
                self._customers[cfg.customer_id] = cfg
        devices_file = self.config_base / "devices" / "special_devices.yaml"
        if devices_file.exists():
            with devices_file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            doc = _DeviceOverridesFile.model_validate(data)
            self._device_overrides = doc.device_overrides

    def reload(self) -> None:
        self._customers.clear()
        self._device_overrides.clear()
        self._load()

    # ---- public Protocol surface ----

    def get_list_name(self, entity: str, scope: ListScope) -> str:
        self._require_canonical_entity(entity, scope)
        if scope.device_id:
            override = self._find_override(entity, scope)
            if override is not None and override.list_name:
                return self._substitute_placeholders(override.list_name, scope)
        cfg = self._require_customer(scope.customer_id, entity)
        entry = cfg.lists.get(entity)
        if entry is None:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": scope.customer_id,
                    "device": scope.device_id or "",
                },
            )
        return self._substitute_placeholders(entry.name, scope)

    @staticmethod
    def _substitute_placeholders(name: str, scope: ListScope) -> str:
        """Resolve <customer_id> / <device_id> template tokens in list names.

        Added 2026-06-27 per architect Path A SP-read probe finding: MMK.yaml
        had Deliverables_<customer_id> as a literal placeholder, matching the
        SP UI Engineer's dynamic construction pattern (siteProperties.list.
        deliverables + carrier). HILDA used to send the literal "<customer_id>"
        string to SP -> HTTP 400 (no such list). Substituting at lookup time
        keeps customer YAMLs reusable as templates rather than forcing one
        hardcoded list-name per customer.

        Supports tokens:
          <customer_id>  -> scope.customer_id   (required; always present)
          <device_id>    -> scope.device_id     (only when set; left untouched
                                                 when None to surface mis-scope
                                                 as visibly wrong list names)
        """
        out = name.replace("<customer_id>", scope.customer_id)
        if scope.device_id is not None:
            out = out.replace("<device_id>", scope.device_id)
        return out

    def get_column_map(self, entity: str, scope: ListScope) -> dict[str, str]:
        self._require_canonical_entity(entity, scope)
        cfg = self._require_customer(scope.customer_id, entity)
        entry = cfg.lists.get(entity)
        if entry is None:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": scope.customer_id,
                    "device": scope.device_id or "",
                },
            )
        merged: dict[str, str] = dict(entry.columns)
        if scope.device_id:
            override = self._find_override(entity, scope)
            if override is not None and override.columns:
                merged.update(override.columns)
        return merged

    def to_sp_fields(
        self,
        entity: str,
        scope: ListScope,
        canonical: dict[str, Any],
    ) -> dict[str, Any]:
        col_map = self.get_column_map(entity, scope)
        out: dict[str, Any] = {}
        for k, v in canonical.items():
            if k not in col_map:
                raise PipelineError(
                    "SHP-E003",
                    context={
                        "field": k,
                        "entity": entity,
                        "customer": scope.customer_id,
                    },
                )
            out[col_map[k]] = v
        return out

    def from_sp_fields(
        self,
        entity: str,
        scope: ListScope,
        sp_fields: dict[str, Any],
    ) -> dict[str, Any]:
        col_map = self.get_column_map(entity, scope)
        reverse = {sp: canonical for canonical, sp in col_map.items()}
        return {reverse[k]: v for k, v in sp_fields.items() if k in reverse}

    # ---- internals ----

    def _require_customer(self, customer_id: str, entity: str) -> _CustomerListConfig:
        if customer_id not in self._customers:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": customer_id,
                    "device": "",
                },
            )
        return self._customers[customer_id]

    def _require_canonical_entity(self, entity: str, scope: ListScope) -> None:
        """Reject legacy / unknown entities up-front per architect Q1 lock
        2026-06-25 + [D-104]. HILDA's SP scope is exactly the 3-entity set
        {delivery_items, milestones, projects}; everything else is SP UI
        engineer's display surface (or Postgres-internal per FR-42)."""
        if entity not in _CANONICAL_ENTITIES:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": scope.customer_id,
                    "device": scope.device_id or "",
                },
            )

    def _find_override(
        self, entity: str, scope: ListScope
    ) -> _DeviceOverride | None:
        for o in self._device_overrides:
            if (
                o.customer_id == scope.customer_id
                and o.device_id == scope.device_id
                and o.entity == entity
            ):
                return o
        return None
