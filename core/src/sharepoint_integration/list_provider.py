"""SharePointListProvider Protocol + FileBasedListProvider boilerplate.

Pure lookup service — no HTTP, no side effects. customizations/ may provide
non-file-based implementations (e.g. DB-backed). Anchors [D-020].
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.config import ListScope


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

    customer_slug: str
    lists: dict[str, _ListEntry]


class _DeviceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_slug: str
    device_slug: str
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
                self._customers[cfg.customer_slug] = cfg
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
        if scope.device_slug:
            override = self._find_override(entity, scope)
            if override is not None and override.list_name:
                return override.list_name
        cfg = self._require_customer(scope.customer_slug, entity)
        entry = cfg.lists.get(entity)
        if entry is None:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": scope.customer_slug,
                    "device": scope.device_slug or "",
                },
            )
        return entry.name

    def get_column_map(self, entity: str, scope: ListScope) -> dict[str, str]:
        cfg = self._require_customer(scope.customer_slug, entity)
        entry = cfg.lists.get(entity)
        if entry is None:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": scope.customer_slug,
                    "device": scope.device_slug or "",
                },
            )
        merged: dict[str, str] = dict(entry.columns)
        if scope.device_slug:
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
                        "customer": scope.customer_slug,
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

    def _require_customer(self, customer_slug: str, entity: str) -> _CustomerListConfig:
        if customer_slug not in self._customers:
            raise PipelineError(
                "SHP-E002",
                context={
                    "entity": entity,
                    "customer": customer_slug,
                    "device": "",
                },
            )
        return self._customers[customer_slug]

    def _find_override(
        self, entity: str, scope: ListScope
    ) -> _DeviceOverride | None:
        for o in self._device_overrides:
            if (
                o.customer_slug == scope.customer_slug
                and o.device_slug == scope.device_slug
                and o.entity == entity
            ):
                return o
        return None
