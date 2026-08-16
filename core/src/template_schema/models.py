"""Pydantic base models for HILDA entities + CustomerSchema contract.

Anchors FR-1–7, FR-28, FR-29, FR-30, FR-39–41, FR-77, FR-78, FR-81, FR-82, FR-83, FR-88,
NFR-14, NFR-21, [D-014], [D-018], [D-028], [D-051], [D-053], [D-054], [D-060], [D-068],
[D-080], [D-085], [D-086], [D-088], [D-091].

2026-06-21 architect cascade applied: 4-field owner identity per FR-88 + [D-086];
nested item_description list-of-lists per FR-82 architect lock; TGGroupBase dropped per
[D-051] denormalization; delivery_path_template + customer_delivery_info at customer
level per FR-77 + NFR-21 §6; DefaultWorkItemConfig FR-78 hardcoded inventory expanded;
path_slug -> path_id rename; button-trigger timestamps on MilestoneBase per FR-6/FR-8/FR-64.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    Hardcoded inventory per FR-78 architect lock 2026-06-21 (expanded):
    - tg_name = "_unrouted" (system-reserved TG)
    - item_type = "Default" (PascalCase per SP UI engineer lock 2026-06-23)
    - tg_path_id = "_unrouted" (first segment of FR-86 _unrouted/<inferred_tg_name>/ NSD path)
    - item_path_id = None (no per-WI item subfolder; documents fan out under
      per-document <inferred_tg_name>)
    - force_tracking_enabled = False (the one explicit exception to FR-81
      column-default True; no tracking_modality, no owner, no outreach surface)
    - tracking_modality = None (null — no modality)
    - owner_corp_usa_email / owner_corp_email / owner_corp_id / owner_name = None
    - milestone_gating = True (always True; YAML template's milestone_gating ignored)
    - no_customer_upload = True (cannot upload to carrier portal per FR-77)
    - review_required = False
    - doc_count = 0
    """

    # Identity:
    tg_name:               Literal["_unrouted"]     = "_unrouted"
    item_name:             str                       = "Unrouted Documents"
    item_type:             Literal["Default"]        = "Default"
    # Path components per FR-78 + FR-86 (added 2026-06-21):
    tg_path_id:            Literal["_unrouted"]      = "_unrouted"
    item_path_id:          None                      = None
    # Tracking / outreach gates per FR-78 + FR-81 lock 2026-06-21:
    force_tracking_enabled: Literal[False]           = False
    tracking_modality:     None                      = None
    # Owner identity per FR-78 + FR-88 (all 4 fields null):
    owner_corp_usa_email:  None                      = None
    owner_corp_email:      None                      = None
    owner_corp_id:         None                      = None
    owner_name:            None                      = None
    # Workflow gates per FR-78 hardcoded invariants:
    milestone_gating:      Literal[True]             = True
    no_customer_upload:    Literal[True]             = True
    review_required:       Literal[False]            = False
    review_status:         Literal["not_required"]   = "not_required"
    rules_paused:          Literal[False]            = False    # default WI is never rule-paused per FR-78 hardcoded inventory + rule_engine D5 cascade 2026-06-23
    doc_count:             Literal[0]                = 0
    # Structural:
    sort_order_strategy:   Literal["max_plus_1", "fixed"] = "max_plus_1"
    sort_order_fixed:      int | None                = None
    not_editable:          bool                      = True
    not_deletable:         bool                      = True


class FolderRoutingEntry(BaseModel):
    """Per FR-77 Type-2 routing — single (ingress_folder → item_no) mapping.

    Naming: `ingress_folder` = inbound NSD-side folder where the document arrives
    (HILDA-PC local path). Distinct from `target_folder` on DeliveryItemBase which
    refers to OUTBOUND customer-portal upload destination (carrier-facing per FR-77).
    Inbound and outbound folder namespaces must never be conflated.
    """

    ingress_folder: str
    item_no:        int
    routing_notes:  str | None = None


class TGFolderRouting(BaseModel):
    """Per FR-77 — TG-scoped folder routing table. One row per (milestone_id, tg_name).
    Loaded into routing pipeline cache (FR-52 step 3) at tracker creation. Empty list →
    folder routing disabled for this TG.
    """

    milestone_id: str
    tg_name:      str
    entries:      list[FolderRoutingEntry] = []


class TagCatalogEntry(BaseModel):
    """Per FR-82 — single tag in the customer's tag catalog.
    Validated against DeliveryItemBase.item_description tag-sets on ingest; unknown tags
    raise TSC-W003 (warning, not error).
    """

    tag:         str
    description: str | None = None
    color:       str | None = None   # optional UI hint (hex code) for dashboard chips


# ----------------------------------------------------------------------------
# Entity base models
# ----------------------------------------------------------------------------


class DeviceBase(_Base):
    """Per-device canonical fields. NOTE: `assigned_pm_id` here is the template-time
    expected PM identity (informational); at runtime HILDA resolves PM 3-tuple via
    per-customer Projects_<customer_id> lookup per [D-088] + NFR-21 2026-06-21 amendment.
    The template-time value may be authoritative for setup_milestone provisioning logic;
    runtime always defers to the SP Projects list.
    """

    device_id:          str
    device_name:        str
    customer_id:        str
    assigned_pm_id:     str | None = None
    status:             str       = "Not Started"
    template_id:        str | None = None
    path_id:            str       # renamed from path_slug 2026-06-21
    target_launch_date: date | None = None

    @field_validator("path_id")
    @classmethod
    def _v_path_id(cls, v: str) -> str:
        return _slug_validator(v)

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        return validate_in_registry(
            DeliveryStateRegistry, v, registry_name="DeliveryState"
        )


class MilestoneBase(_Base):
    """Per-milestone canonical fields. Milestones SP list is GLOBAL per architect lock
    2026-06-21 (intentional asymmetry — milestone names like LE-2 reused across carriers;
    Projects + Deliverables are per-customer). Composite uniqueness enforced by SP UI
    engineer's setup_milestone via (carrier, project_id, project_model, Title).

    Includes HILDA-managed runtime button-trigger timestamps (NOT authored in template;
    written by SP UI engineer's web part on button click; read by HILDA via SP-alert
    per FR-84 Source B routing).
    """

    milestone_id:   str          # SP intrinsic auto-Counter Id
    carrier:        str          # = customer_id; denormalized per Milestones row
    project_id:     int          # SP intrinsic Id of Projects_<customer_id> row per [D-088]
    project_model:  str          # = device_id; denormalized per Milestones row
    milestone_name: str          # = Title (SP intrinsic) per [D-091] YAML key naming
    sort_order:     int
    target_date:    date | None       = None    # sole authoritative deadline per [D-085]
    status:         MilestoneStatus
    path_id:        str          # renamed from path_slug 2026-06-21
    # Phase additions per [D-028] + [D-053]:
    email_cc_list:  list[dict] | None = None
    default_work_item_config: DefaultWorkItemConfig | None = None
    # HILDA-managed runtime button-trigger timestamps (added 2026-06-21):
    milestone_collection_started_at:    datetime | None = None
    milestone_submission_triggered_at:  datetime | None = None
    closed_all_items_triggered_at:      datetime | None = None
    refresh_requested_at:               datetime | None = None  # Ph-2 per [D-089]
    milestone_completion_pct:           int             = 0
    # Download-package fields (Ph-2 per [D-089]):
    download_package_request_timestamp: datetime | None = None
    download_package_status:            str | None      = None
    download_package_url:               str | None      = None
    download_package_generated_at:      datetime | None = None

    @field_validator("path_id")
    @classmethod
    def _v_path_id(cls, v: str) -> str:
        return _slug_validator(v)


# TGGroupBase DROPPED 2026-06-21 per [D-051] denormalization + architect lock.
# All TG-level fields now live on DeliveryItemBase directly. TG-grouping semantics
# preserved via shared tg_name across items in the same milestone.


class DeliveryItemBase(_Base):
    """Per-work-item canonical fields. 2026-06-21 architect cascade applied:

    - Owner identity is 4-field free-form text per FR-88 + [D-080] + [D-086]
      (owner_corp_usa_email, owner_corp_email, owner_corp_id, owner_name);
      single owner_email removed.
    - TG fields denormalized onto this model per [D-051] (TGGroups SP list dropped;
      TGGroupBase Pydantic model removed; TG fields live on each row).
    - item_description is nested JSON list-of-lists per FR-82 (per-document tag-sets);
      TSC-W007 subset detection + TSC-W008 doc_count consistency enforced.
    - force_tracking_enabled is sole per-item tracking gate (SP BOOL column-default=true)
      per FR-81 option (a) lock; per-TG tracking_enabled no longer exists.
    - target_folder is template-author-supplied (per-item sub-path under milestone HOME);
      runtime composes upload destination = customer_delivery_info + delivery_path_template
      (expanded) + target_folder per FR-77 + NFR-21 §6 amendment 2026-06-21.
    """

    item_id:                         str
    item_no:                         int
    # 2026-07-01 architect lock -- dashboard /docs/<customer_id>/<sp_id> lookup.
    # sp_id is SP's Deliverables list row Id; HILDA's delivery_item_id is a
    # composite string primary key -- the two are NOT the same integer.
    # Populated in import_deliverable_tracker_task via SP READ by natural key
    # (carrier + project_model + item_no). Nullable for rows created before this
    # cascade + defensive against transient SP READ failures at import time.
    sp_id:                           int | None = None
    milestone_id:                    str   # reparented from deliverable_id per [D-028]
    item_name:                       str
    item_description:                list[list[str]] | None = None  # nested tag-sets per FR-82 lock 2026-06-20
    delivery_state:                  str
    prior_delivery_state:            str | None = None   # HILDA-managed per FR-7 Delayed/Blocked exit paths
    expected_completion_date:        date | None = None
    actual_completion_date:          date | None = None
    item_type:                       str
    # 4-field owner identity per FR-88 + [D-080] + [D-086] (free-form, no AD validation).
    # OWNER-1 (2026-08-14): singular fields DEPRECATED in favor of the *_list
    # fields below; kept live for backward compat during OWNER-2..6 reader
    # migration. Ingest dual-writes: singular = first entry from list.
    # OWNER-7 drops these + renames the _list fields back to unsuffixed names.
    owner_corp_usa_email:            str | None = None   # DEPRECATED (OWNER-1); use owner_corp_usa_email_list. Preferred outreach recipient per [D-080].
    owner_corp_email:                str | None = None   # DEPRECATED (OWNER-1); use owner_corp_email_list. Fallback outreach recipient.
    owner_corp_id:                   str | None = None   # DEPRECATED (OWNER-1); use owner_corp_id_list. Corp directory ID; PLM grouping key per FR-5 + [D-035].
    owner_name:                      str | None = None   # DEPRECATED (OWNER-1); use owner_name_list.
    # OWNER-1 (2026-08-14): multi-value owner identity lists (co-owners /
    # backup contacts). Populated by ingest via _split_owner_list on the
    # semicolon-separated SP text field ("alice@corp; bob@corp;"). All 4
    # lists are index-aligned per person -- empty string in a slot for
    # unknown attributes. Readers migrate from singular -> list in OWNER-2..6.
    owner_name_list:                 list[str] = Field(default_factory=list)
    owner_corp_email_list:           list[str] = Field(default_factory=list)
    owner_corp_usa_email_list:       list[str] = Field(default_factory=list)
    owner_corp_id_list:              list[str] = Field(default_factory=list)
    tracking_modality:               list[str]            # MULTI-VALUE per [D-037]
    actual_item_info:                str | None = None
    plm_id:                          str | None = None
    # Form-factor flags (7 flags per [D-084]):
    handset:                         bool        = False
    tablet:                          bool        = False
    wearable:                        bool        = False
    ir:                              bool        = False
    osmr:                            bool        = False
    rmr:                             bool        = False
    hmr_smr:                         bool        = False
    # Carrier-portal delivery (customer_delivery_modality moved to CustomerTemplateBase
    # per D-126 architect Q2 lock 2026-06-26 -- one modality per customer; no_customer_upload
    # is the sole upload gate per FR-80):
    customer_delivery_info:          str | None = None   # base URL denormalized per-item from CustomerTemplateBase
    customer_delivery_credential_id: str | None = None
    # Outreach / status tracking:
    owner_status_note:               str | None = None
    comment:                         str | None = None
    last_updated:                    datetime
    last_owner_contacted:            datetime | None = None
    last_reminder_triggered_at:      datetime | None = None  # HILDA + SP UI dual-writer per NFR-21 §5 amendment 2026-06-21
    last_owner_response_at:          datetime | None = None
    reminder_count:                  int             = 0
    sort_order:                      int
    path_id:                         str   # renamed from path_slug 2026-06-21
    # FR-7 / FR-53 / FR-70 fields:
    doc_count:                       int   = 1
    review_required:                 bool  = False
    review_status:                   str   = "not_required"  # 4-value enum: pending/complete/not_required/failed
    item_completion_pct:             int   = 0
    email_cc_list:                   list[dict] | None = None
    milestone_gating:                bool  = True       # renamed back from is_milestone_gating 2026-06-21 per xlsx + spec convergence
    no_customer_upload:              bool  = False
    force_tracking_enabled:          bool  = True       # SP BOOL column-default=true per FR-81 option (a) lock 2026-06-20
    manual_triage_required:          bool  = False
    rules_paused:                    bool  = False  # FR-31 sub-1 per-item rule pause per rule_engine D5 cascade 2026-06-23; TPM-writable via SP UI; HILDA reads at rule eval (skip all rules when True). SP-side: Choice(Yes/No) column on Deliverables_<customer_id>.
    # FR-77 routing fields:
    ingress_folder:                  str | None = None
    target_folder:                   str | None = None   # template-author-supplied per-item sub-path under milestone HOME
    # FR-78 path components:
    item_path_id:                    str | None = None
    tg_path_id:                      str | None = None
    # TG-denormalized fields per [D-051]:
    tg_name:                         str | None = None
    ingress_nsd:                     str         = "None"   # Choice per FR-13 + SP UI engineer lock 2026-06-23: None / NSD1 / NSD2
    folder_routing_enabled:          bool        = False
    tg_email_group_alias:            str | None = None
    tg_owner_name:                   str | None = None
    tg_owner_corp_usa_email:         str | None = None
    tg_owner_corp_email:             str | None = None
    tg_owner_corp_id:                str | None = None
    corp_id_list:                    list[str] | None = None
    # JIRA polling state (Ph-2 per FR-25 (b)):
    jira_open_ticket_count:          int         = 0
    jira_ticket_summary_json:        str | None = None
    # PM-approval recording per [D-068]:
    pm_approval_at:                  datetime | None = None
    pm_approval_pm_id:               str | None      = None
    # owner_intent_closed_at -- timestamp owner replied "Closed" while
    # doc_count_reached was still false (guard_denied on OwnerClosed entry).
    # Persisted so the reconcile_owner_intent_on_state_change rule can
    # auto-advance to OwnerClosed when docs catch up. Cleared on:
    #   - OwnerClosed entry (consumed by transition)
    #   - subsequent owner reply status=Open (owner revokes intent)
    # Architect 2026-06-29 race-resolution: "owner can open after closure".
    owner_intent_closed_at:          datetime | None = None
    # DRR-V2-6c (2026-08-05): dropped separate owner_completion_date;
    # owner-typed completion dates now write to the existing
    # `actual_completion_date` field above so the DRR excel has a single
    # canonical source regardless of whether TPM or owner supplied it.
    # TPM-resolution fields per FR-83 / FR-87:
    tpm_reassignment_target_item_id: int | None = None
    tpm_resolved_doc_type:           str | None = None   # LIST/Choice per architect direction 2026-06-21
    tpm_revision_resolution:         str | None = None   # Ph-2
    # FR-87 audit fields:
    list_documents_url_prefix:       str | None = None
    per_item_rule_overrides:         str | None = None   # Ph-2

    @field_validator("path_id")
    @classmethod
    def _v_path_id(cls, v: str) -> str:
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

    @field_validator("review_status")
    @classmethod
    def _v_review_status(cls, v: str) -> str:
        # 4-value enum per FR-53 / FR-60 (architect lock 2026-06-19):
        valid = {"pending", "complete", "not_required", "failed"}
        if v not in valid:
            raise ValueError(
                f"review_status must be one of {sorted(valid)}; got {v!r}"
            )
        return v

    @field_validator("ingress_nsd")
    @classmethod
    def _v_ingress_nsd(cls, v: str) -> str:
        # Choice per FR-13 + SP UI engineer lock 2026-06-23: None / NSD1 / NSD2
        valid = {"None", "NSD1", "NSD2"}
        if v not in valid:
            raise ValueError(
                f"ingress_nsd must be one of {sorted(valid)}; got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _v_confirmation_no_customer_upload(self) -> "DeliveryItemBase":
        """Per [D-053] + tracker MODULE.md invariant — Confirmation items MUST have
        no_customer_upload=True. Emits TSC-W004 warning (not blocking) per
        MODULE.md Invariant; ops triage."""
        if self.item_type == ItemType.CONFIRMATION.value and not self.no_customer_upload:
            msg = get_code("TSC-W004").message.format(item_id=self.item_id)
            _log.warning(msg)
        return self

    @model_validator(mode="after")
    def _v_default_tag_isolation(self) -> "DeliveryItemBase":
        """Per architect 2026-07-22 refinement to Ph-1 attachment routing:
        the reserved literal "default" tag MUST appear as its own singleton
        tag-set entry (i.e., `["default"]`) — never mixed with other tags in
        the same tag-set.

        Valid:
          [["waiver"], ["default"]]              — two tag-sets; "default" is standalone
          [["default"]]                          — single "default" tag-set
        Invalid:
          [["waiver", "default"]]                — "default" mixed with "waiver"
          [["default", "sig_report"]]            — "default" mixed with "sig_report"

        Runtime routing treats an item with `["default"]` as its own tag-set
        entry as the TG-scoped default catch-all (TG_DEFAULT_MULTIMATCH /
        TG_DEFAULT_NOMATCH resolutions).

        D-154 architect 2026-07-26 — same isolation rule applies to the
        reserved literal `all-15-digits-imei`. Matches iff the doc's filename
        is exactly 15 digits + Excel extension. Standalone-only so template
        authors can't accidentally combine it with substring tags (which
        would produce ambiguous match semantics).
        """
        _RESERVED_LITERALS = {"default", "all-15-digits-imei"}
        if not self.item_description:
            return self
        for i, tag_set in enumerate(self.item_description):
            if not isinstance(tag_set, list):
                continue
            reserved_hits = {
                t.strip().lower() for t in tag_set
                if isinstance(t, str) and t.strip().lower() in _RESERVED_LITERALS
            }
            if reserved_hits and len(tag_set) > 1:
                literal = next(iter(reserved_hits))
                raise ValueError(
                    f"item {self.item_id}: item_description[{i}]={tag_set!r} — "
                    f"reserved literal '{literal}' tag must appear as a standalone "
                    f"tag-set entry (e.g. `[\"{literal}\"]`), never mixed with other tags"
                )
        return self

    @model_validator(mode="after")
    def _v_doc_count_consistency(self) -> "DeliveryItemBase":
        # TSC-W008 warning silenced 2026-07-30: doc_count varies per study while
        # item_description is per-work-item; the equality check is not a valid
        # invariant. Revisit the whole rule (may be removed entirely).
        return self


class CustomerTemplateBase(_Base):
    """Top-level customer template per FR-39/FR-40 + [D-091] YAML structure.
    Authored manually by PM team lead in Ph-1; generated by template_schema_ingestor
    per [D-010] + [D-018] in Ph-2+.

    Reads from `customizations/template_schemas/<customer_id>/template.yaml`.
    No Deliverable level per [D-028]; DeliveryItems nest directly under Milestones.
    """

    template_id:      str
    customer_id:      str          # carrier code; HILDA-canonical (YAML top-level key per [D-091])
    template_name:    str
    template_version: int
    # HILDA-config-only fields (NOT in SP per [D-083]):
    customer_jira_url:           str | None = None   # read at startup per FR-25 (b)
    customer_delivery_modality:  str | None = None   # MOVED here from DeliveryItemBase per D-126 architect Q2 lock 2026-06-26; one modality per customer (e.g., "GoogleDrive"); subclass-implicit at runtime; validated against CustomerDeliveryModalityRegistry
    # Customer-level carrier-portal delivery config (added 2026-06-21 per FR-77 + NFR-21 §6):
    customer_delivery_info:   str | None = None   # base URL (e.g. "drive.google.com"); denormalized per-item at setup_milestone
    delivery_path_template:   str | None = None   # template producing milestone HOME path; literal segments + {placeholders}
    # Devices + milestones:
    devices:    dict[str, DeviceBase] = {}    # YAML key = device_id per [D-091]
    milestones: list[MilestoneBase]   = []
    is_active:  bool                  = True

    @field_validator("customer_delivery_modality")
    @classmethod
    def _v_cust_modality(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_in_registry(
            CustomerDeliveryModalityRegistry,
            v,
            registry_name="CustomerDeliveryModality",
        )


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

    Stored as customizations/template_schemas/<customer_id>/schema.yaml.
    """

    customer_slug:    str            # historical name; corresponds to customer_id per [D-091]
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
