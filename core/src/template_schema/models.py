"""Pydantic base models for HILDA entities + CustomerSchema contract.

Anchors FR-1–7, FR-39–41, NFR-14, [D-014], [D-018].
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from core.src.diagnostics import PipelineError
from core.src.template_schema.enums import MilestoneStatus, RuleActionType, RuleScope
from core.src.template_schema.registry import (
    CustomerDeliveryModalityRegistry,
    DeliveryStateRegistry,
    ItemTypeRegistry,
    TrackingModalityRegistry,
    validate_in_registry,
)
from core.src.template_schema.slug import validate_slug as _validate_slug_raw


def _slug_validator(v: str) -> str:
    """Pydantic-compatible slug validator: PipelineError → ValueError."""
    try:
        return _validate_slug_raw(v)
    except PipelineError as e:
        raise ValueError(str(e)) from e


class _Base(BaseModel):
    """Permissive base — runtime modules subclass and add persistence fields."""

    model_config = ConfigDict(extra="allow")


class DeviceBase(_Base):
    device_id: str
    device_name: str
    customer_id: str
    assigned_pm_id: str
    status: str
    template_id: str | None = None
    path_slug: str
    target_launch_date: date | None = None

    @field_validator("path_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        return validate_in_registry(
            DeliveryStateRegistry, v, registry_name="DeliveryState"
        )


class MilestoneBase(_Base):
    milestone_id: str
    device_id: str
    milestone_name: str
    sort_order: int
    target_date: date | None = None
    status: MilestoneStatus
    path_slug: str

    @field_validator("path_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)


class DeliverableBase(_Base):
    deliverable_id: str
    milestone_id: str
    deliverable_name: str
    sort_order: int
    status: MilestoneStatus
    completion_pct: int = 0
    path_slug: str

    @field_validator("path_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)

    @field_validator("completion_pct")
    @classmethod
    def _v_pct(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"completion_pct must be 0..100, got {v}")
        return v


class DeliveryItemBase(_Base):
    item_id: str
    deliverable_id: str
    item_name: str
    description: str | None = None
    delivery_state: str
    expected_completion_date: date | None = None
    item_type: str
    owner_name: str | None = None
    owner_email: str | None = None
    tracking_modality: str
    actual_item_info: str | None = None
    customer_delivery_modality: str
    customer_delivery_info: str | None = None
    customer_delivery_credential_id: str | None = None
    comment: str | None = None
    last_updated: datetime
    last_owner_contacted: datetime | None = None
    sort_order: int
    path_slug: str

    @field_validator("path_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)

    @field_validator("delivery_state")
    @classmethod
    def _v_state(cls, v: str) -> str:
        return validate_in_registry(DeliveryStateRegistry, v, registry_name="DeliveryState")

    @field_validator("item_type")
    @classmethod
    def _v_type(cls, v: str) -> str:
        return validate_in_registry(ItemTypeRegistry, v, registry_name="ItemType")

    @field_validator("tracking_modality")
    @classmethod
    def _v_track(cls, v: str) -> str:
        return validate_in_registry(
            TrackingModalityRegistry, v, registry_name="TrackingModality"
        )

    @field_validator("customer_delivery_modality")
    @classmethod
    def _v_cust(cls, v: str) -> str:
        return validate_in_registry(
            CustomerDeliveryModalityRegistry,
            v,
            registry_name="CustomerDeliveryModality",
        )


class CustomerTemplateBase(_Base):
    template_id: str
    customer_id: str
    template_name: str
    template_version: int
    milestones: list[MilestoneBase] = []
    is_active: bool = True


class AutomationRuleBase(_Base):
    rule_id: str
    rule_name: str
    scope: RuleScope
    scope_id: str | None = None
    trigger_event: str
    trigger_condition: dict[str, Any] = {}
    action_type: RuleActionType
    action_parameters: dict[str, Any] = {}
    priority: int = 100
    is_active: bool = True


# --- CustomerSchema (ingestor → runtime contract) ----------------------------


_COL_TYPE = Literal["str", "int", "float", "bool", "date", "email", "enum"]


class ColumnMapping(BaseModel):
    source: str
    canonical: str
    col_type: _COL_TYPE
    required: bool = False
    format: str | None = None
    enum_values: list[str] | None = None


_ENTITY_NAME = Literal["device", "milestone", "deliverable", "delivery_item"]


class EntitySchemaConfig(BaseModel):
    entity: _ENTITY_NAME
    header_row: int = 1
    columns: list[ColumnMapping]


class CustomerSchema(BaseModel):
    """Output contract of template_schema_ingestor; input contract for runtime modules.

    Stored as customizations/template_schemas/<customer_slug>/schema.yaml.
    """

    customer_slug: str
    schema_version: int
    entity_hierarchy: list[EntitySchemaConfig]
    sp_list_mappings: dict[str, str] = {}

    @field_validator("customer_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)

    @classmethod
    def load(cls, customer_slug: str, base_path: Path) -> "CustomerSchema":
        path = base_path / customer_slug / "schema.yaml"
        if not path.exists():
            raise PipelineError(
                "TSC-E001",
                context={"customer": customer_slug, "reason": f"file not found: {path}"},
            )
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PipelineError(
                "TSC-E001",
                context={"customer": customer_slug, "reason": f"yaml parse: {e}"},
                cause=e,
            ) from e
        try:
            return cls.model_validate(data)
        except Exception as e:
            raise PipelineError(
                "TSC-E001",
                context={"customer": customer_slug, "reason": str(e)},
                cause=e,
            ) from e

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )
