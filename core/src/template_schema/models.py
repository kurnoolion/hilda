"""Pydantic base models for HILDA entities + CustomerSchema contract.

Anchors FR-1–7, FR-28, FR-29, FR-30, FR-39–41, FR-77, FR-78, FR-82, FR-83,
NFR-14, [D-014], [D-018], [D-028], [D-051], [D-053], [D-054], [D-060], [D-068].
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from core.src.diagnostics import PipelineError, get_code
from core.src.template_schema.enums import (
    ItemType,
    MilestoneStatus,
    RuleActionType,
    RuleScope,
)
from core.src.template_schema.registry import (
    CustomerDeliveryModalityRegistry,
    DeliveryStateRegistry,
    ItemTypeRegistry,
    TrackingModalityRegistry,
    validate_in_registry,
)
from core.src.template_schema.slug import validate_slug as _validate_slug_raw

_log = logging.getLogger(__name__)


def _slug_validator(v: str) -> str:
    """Pydantic-compatible slug validator: PipelineError → ValueError."""
    try:
        return _validate_slug_raw(v)
    except PipelineError as e:
        raise ValueError(str(e)) from e


class _Base(BaseModel):
    """Permissive base — runtime modules subclass and add persistence fields."""

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------
# Small helper models (no inter-dependencies among themselves)
# ----------------------------------------------------------------------------


class DefaultWorkItemConfig(BaseModel):
    """Per FR-78 + [D-053] — configures the auto-instantiated default work-item
    per MILESTONE. Exactly one default work-item per milestone (NOT per TG).

    Routing model: when the FR-52 pipeline cannot resolve a specific work-item,
    the document lands here. The document's TG IS knowable from the inbound
    channel (NSD ingress folder per TGGroupBase.ingress_nsd; email sender via
    email_group_alias / owner_email lookup; PLM-id via DeliveryItemBase.plm_id
    reverse-lookup) and is recorded on the document record as
    DocumentIndexRow.inferred_tg_name (storage module), NOT on the default
    work-item. FR-83 TPM-manual reassignment uses inferred_tg_name to shortlist
    candidate work-items within that TG.
    """

    tg_name:             Literal["_unrouted"]     = "_unrouted"
    item_name:           str                       = "Unrouted Documents"
    item_type:           Literal["Default"]       = "Default"
    sort_order_strategy: Literal["max_plus_1", "fixed"] = "max_plus_1"
    sort_order_fixed:    int | None                = None
    not_editable:        bool                      = True
    not_deletable:       bool                      = True


class FolderRoutingEntry(BaseModel):
    """Per FR-77 Type-2 routing — single (ingress_folder → item_no) mapping.

    Naming: `ingress_folder` = inbound NSD-side folder where the document arrives
    (HILDA-PC local path under TGGroupBase.ingress_nsd). Distinct from
    `target_folder` on DeliveryItemBase which refers to OUTBOUND customer-portal
    upload destination (carrier-facing upload path per FR-73 / FR-19). Inbound
    and outbound folder namespaces must never be conflated.
    """

    ingress_folder: str
    item_no:        int
    routing_notes:  str | None = None


class TGFolderRouting(BaseModel):
    """Per FR-77 — TG-scoped folder routing table. One row per (milestone_id, tg_name).
    Loaded into routing pipeline cache (FR-52 step 3) at tracker creation; refreshed
    on TGGroupBase update. Empty list → folder routing disabled for this TG.
    """

    milestone_id: str
    tg_name:      str
    entries:      list[FolderRoutingEntry] = []


class TagCatalogEntry(BaseModel):
    """Per FR-82 (revised 2026-06-05) — single tag in the customer's tag catalog.
    Validated against DeliveryItemBase.item_description on ingest; unknown tags
    raise TSC-W003 (warning, not error).
    """

    tag:         str
    description: str | None = None
    color:       str | None = None   # optional UI hint (hex code) for dashboard chips


# ----------------------------------------------------------------------------
# Entity base models
# ----------------------------------------------------------------------------


class DeviceBase(_Base):
    device_id:          str
    device_name:        str
    customer_id:        str
    assigned_pm_id:     str
    status:             str
    template_id:        str | None = None
    path_slug:          str
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
    milestone_id:   str
    device_id:      str
    milestone_name: str
    sort_order:     int
    target_date:    date | None       = None
    status:         MilestoneStatus
    path_slug:      str
    # Phase additions per [D-028] + [D-053]:
    email_cc_list:  list[dict] | None = None
    default_work_item_config: DefaultWorkItemConfig | None = None

    @field_validator("path_slug")
    @classmethod
    def _v_slug(cls, v: str) -> str:
        return _slug_validator(v)


class TGGroupBase(_Base):
    """Per [D-049] (ODF) + [D-051] (TGGroups SP list). One row per
    (milestone_id, tg_name) — applies to all DeliveryItems sharing that tg_name
    in the milestone. Source data: customizations/template_schemas/<slug>/tg_groups.yaml.
    """

    tg_group_id:        str
    milestone_id:       str
    tg_name:            str
    tg_owner_name:      str | None       = None
    tg_owner_email:     str | None       = None
    email_group_alias:  str | None       = None
    corp_id_list:       list[str] | None = None
    default_cc_list:    list[dict] | None = None
    # Per-TG fields per FR-77 / FR-78 / [D-053] / [D-054]:
    ingress_nsd:           Literal["NSD1", "NSD2"] = "NSD1"
    folder_routing_enabled: bool = False
    tracking_enabled:       bool = True


class DeliveryItemBase(_Base):
    """Per FR-7 + [D-053] + [D-068]. Reparented from `deliverable_id` to `milestone_id`
    per [D-028] (Deliverable level removed). 4-value ItemType per [D-053]. 11-value
    DeliveryState per FR-7.
    """

    item_id:                         str
    item_no:                         int
    milestone_id:                    str   # reparented from deliverable_id per [D-028]
    tg_name:                         str | None = None
    item_name:                       str
    item_description:                str | None = None
    delivery_state:                  str
    expected_completion_date:        date | None = None
    actual_completion_date:          date | None = None
    item_type:                       str
    owner_name:                      str | None = None
    owner_email:                     str | None = None
    tracking_modality:               list[str]   # MULTI-VALUE per [D-037]
    actual_item_info:                str | None = None
    plm_id:                          str | None = None
    handset:                         bool        = False
    tablet:                          bool        = False
    wearable:                        bool        = False
    mr:                              bool        = False
    hmr_smr:                         bool        = False
    customer_delivery_modality:      str
    customer_delivery_info:          str | None = None
    customer_delivery_credential_id: str | None = None
    owner_status_note:               str | None = None
    comment:                         str | None = None
    last_updated:                    datetime
    last_owner_contacted:            datetime | None = None
    sort_order:                      int
    path_slug:                       str
    # Phase 2 additions (FR-2 / FR-7 / FR-53 / FR-70):
    doc_count:                       int   = 1
    review_required:                 bool  = False
    review_status:                   str   = "not_required"
    item_completion_pct:             int   = 0
    email_cc_list:                   list[dict] | None = None
    milestone_gating:                bool  = False
    # Phase 3 additions ([D-053] / [D-054] / FR-77 / FR-78):
    no_customer_upload:              bool  = False
    force_tracking_enabled:          bool | None = None
    ingress_folder:                  str | None  = None
    target_folder:                   str | None  = None
    # Phase 4 additions per [D-068] PM-approval recording:
    pm_approval_at:                  datetime | None = None
    pm_approval_pm_id:               str | None       = None

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
    def _v_track(cls, v: list[str]) -> list[str]:
        # Multi-value per [D-037]; each value validated against registry.
        for item in v:
            validate_in_registry(
                TrackingModalityRegistry, item, registry_name="TrackingModality"
            )
        return v

    @field_validator("customer_delivery_modality")
    @classmethod
    def _v_cust(cls, v: str) -> str:
        return validate_in_registry(
            CustomerDeliveryModalityRegistry,
            v,
            registry_name="CustomerDeliveryModality",
        )

    @model_validator(mode="after")
    def _v_confirmation_no_customer_upload(self) -> "DeliveryItemBase":
        """Per [D-053] + tracker MODULE.md invariant — Confirmation items MUST have
        no_customer_upload=True. Emits TSC-W004 warning (not blocking) per
        MODULE.md Invariant; ops triage."""
        if self.item_type == ItemType.CONFIRMATION.value and not self.no_customer_upload:
            msg = get_code("TSC-W004").message.format(item_id=self.item_id)
            _log.warning(msg)
        return self


class CustomerTemplateBase(_Base):
    """Per [D-028] — no Deliverable level; DeliveryItems nest directly under Milestones."""

    template_id:      str
    customer_id:      str
    template_name:    str
    template_version: int
    milestones:       list[MilestoneBase] = []
    is_active:        bool                = True


class AutomationRuleBase(_Base):
    rule_id:           str
    rule_name:         str
    scope:             RuleScope
    scope_id:          str | None       = None
    trigger_event:     str
    trigger_sub_event: str | None       = None   # required when trigger_event == "ItemModified"; else None
    trigger_condition: dict[str, Any]   = {}
    action_type:       RuleActionType
    action_parameters: dict[str, Any]   = {}
    priority:          int              = 100
    is_active:         bool             = True


# --- CustomerSchema (ingestor → runtime contract) ---------------------------


_COL_TYPE = Literal["str", "int", "float", "bool", "date", "email", "enum"]


class ColumnMapping(BaseModel):
    source:      str
    canonical:   str
    col_type:    _COL_TYPE
    required:    bool             = False
    format:      str | None       = None
    enum_values: list[str] | None = None


_ENTITY_NAME = Literal["device", "milestone", "delivery_item"]   # "deliverable" removed per [D-028]


class EntitySchemaConfig(BaseModel):
    entity:     _ENTITY_NAME
    header_row: int = 1
    columns:    list[ColumnMapping]


class CustomerSchema(BaseModel):
    """Output contract of template_schema_ingestor; input contract for runtime modules.

    Stored as customizations/template_schemas/<customer_slug>/schema.yaml.
    """

    customer_slug:    str
    schema_version:   int
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
