"""Unit tests for core.src.template_schema."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.src.diagnostics import PipelineError
from core.src.template_schema import (
    ColumnMapping,
    CustomerDeliveryModality,
    CustomerSchema,
    DeliveryItemBase,
    DeliveryState,
    DeliveryStateRegistry,
    DeviceBase,
    DocType,
    EntitySchemaConfig,
    IngestSource,
    ItemType,
    MilestoneBase,
    MilestoneStatus,
    RuleActionType,
    RuleScope,
    RuleSubTriggerType,
    RuleTriggerType,
    SLUG_PATTERN,
    TrackingModality,
    extend_registry,
    make_slug,
    validate_slug,
)
from core.src.template_schema import TestReportClassification as _TRClassification
from core.src.template_schema import TestReportItemStatus as _TRItemStatus


class TestSlug:
    def test_pattern_accepts_alphanumeric_dash_underscore(self) -> None:
        assert SLUG_PATTERN.match("carrier-alpha")
        assert SLUG_PATTERN.match("CarrierAlpha")
        assert SLUG_PATTERN.match("device_001")
        assert SLUG_PATTERN.match("ABC-123_xyz")

    def test_pattern_rejects_spaces_punctuation(self) -> None:
        assert not SLUG_PATTERN.match("has spaces")
        assert not SLUG_PATTERN.match("dot.in.middle")
        assert not SLUG_PATTERN.match("slash/in/middle")
        assert not SLUG_PATTERN.match("")

    def test_validate_slug_returns_value(self) -> None:
        assert validate_slug("ok-slug") == "ok-slug"

    def test_validate_slug_rejects_bad(self) -> None:
        with pytest.raises(PipelineError) as ei:
            validate_slug("has spaces")
        assert ei.value.code_id == "TSC-E004"

    def test_make_slug_lowercases_and_replaces(self) -> None:
        assert make_slug("Carrier Alpha") == "carrier-alpha"
        assert make_slug("Device #001!") == "device-001"
        assert make_slug("Already-Slug_OK") == "already-slug-ok"

    def test_make_slug_truncates(self) -> None:
        long_name = "x" * 200
        assert len(make_slug(long_name)) == 64

    def test_make_slug_empty_raises(self) -> None:
        with pytest.raises(PipelineError):
            make_slug("")
        with pytest.raises(PipelineError):
            make_slug("!!!")  # all stripped


class TestExtensibilityRegistries:
    def test_initial_registries_seeded_from_enums(self) -> None:
        for s in DeliveryState:
            assert s.value in DeliveryStateRegistry

    def test_extend_registry_adds_value(self) -> None:
        try:
            extend_registry(DeliveryStateRegistry, ["Pending Customer Action"])
            assert "Pending Customer Action" in DeliveryStateRegistry
            # idempotent
            extend_registry(DeliveryStateRegistry, ["Pending Customer Action"])
        finally:
            DeliveryStateRegistry.discard("Pending Customer Action")

    def test_validators_use_registry_not_enum(self) -> None:
        try:
            extend_registry(DeliveryStateRegistry, ["Awaiting Review"])
            di = _make_delivery_item(state="Awaiting Review")
            assert di.delivery_state == "Awaiting Review"
        finally:
            DeliveryStateRegistry.discard("Awaiting Review")


def _make_delivery_item(**overrides: object) -> DeliveryItemBase:
    defaults = dict(
        item_id="i1",
        deliverable_id="d1",
        item_name="x",
        delivery_state=DeliveryState.OPEN.value,
        item_type=ItemType.CONFIRMATION.value,   # updated 2026-06-10 Phase 1 — BINARY removed per [D-053]
        tracking_modality="Email",
        customer_delivery_modality="None",
        last_updated=datetime.now(timezone.utc),
        sort_order=1,
        path_slug="i1-slug",
    )
    defaults.update({"delivery_state": overrides.pop("state", defaults["delivery_state"])})  # alias
    defaults.update(overrides)
    return DeliveryItemBase(**defaults)  # type: ignore[arg-type]


class TestEntityModels:
    def test_device_base_validates_slug(self) -> None:
        with pytest.raises(ValidationError):
            DeviceBase(
                device_id="d1",
                device_name="X",
                customer_id="c1",
                assigned_pm_id="pm1",
                status=DeliveryState.OPEN.value,
                path_slug="has spaces",
            )

    def test_device_base_rejects_unknown_state(self) -> None:
        with pytest.raises(ValidationError):
            DeviceBase(
                device_id="d1",
                device_name="X",
                customer_id="c1",
                assigned_pm_id="pm1",
                status="Imaginary",
                path_slug="device-1",
            )

    def test_device_base_accepts_valid(self) -> None:
        d = DeviceBase(
            device_id="d1",
            device_name="X",
            customer_id="c1",
            assigned_pm_id="pm1",
            status=DeliveryState.OPEN.value,
            path_slug="device-1",
            target_launch_date=date(2026, 12, 1),
        )
        assert d.path_slug == "device-1"

    def test_milestone_base_status_enum(self) -> None:
        m = MilestoneBase(
            milestone_id="m1",
            device_id="d1",
            milestone_name="M1",
            sort_order=1,
            status=MilestoneStatus.NOT_STARTED,
            path_slug="m-1",
        )
        assert m.status == MilestoneStatus.NOT_STARTED

    def test_delivery_item_validates_all_registries(self) -> None:
        di = _make_delivery_item()
        assert di.delivery_state == "Open"
        assert di.item_type == "Confirmation"   # updated 2026-06-10 Phase 1 — was "Binary"; ItemType.BINARY removed per [D-053]
        assert di.tracking_modality == "Email"
        assert di.customer_delivery_modality == "None"

    def test_delivery_item_rejects_bad_modality(self) -> None:
        with pytest.raises(ValidationError):
            _make_delivery_item(tracking_modality="Carrier_Pigeon")

    def test_completion_pct_range(self) -> None:
        from core.src.template_schema import DeliverableBase
        with pytest.raises(ValidationError):
            DeliverableBase(
                deliverable_id="dv1",
                milestone_id="m1",
                deliverable_name="X",
                sort_order=1,
                status=MilestoneStatus.IN_PROGRESS,
                completion_pct=150,
                path_slug="dv-1",
            )


class TestCustomerSchema:
    def _sample(self) -> CustomerSchema:
        return CustomerSchema(
            customer_slug="carrier-alpha",
            schema_version=1,
            entity_hierarchy=[
                EntitySchemaConfig(
                    entity="device",
                    header_row=1,
                    columns=[
                        ColumnMapping(
                            source="Device Name",
                            canonical="device_name",
                            col_type="str",
                            required=True,
                        ),
                        ColumnMapping(
                            source="PM Owner",
                            canonical="assigned_pm_id",
                            col_type="str",
                            required=True,
                        ),
                    ],
                ),
            ],
            sp_list_mappings={"device_name": "Title", "assigned_pm_id": "PM_Owner"},
        )

    def test_construct_valid(self) -> None:
        s = self._sample()
        assert s.customer_slug == "carrier-alpha"
        assert len(s.entity_hierarchy) == 1

    def test_yaml_round_trip(self, tmp_path: Path) -> None:
        original = self._sample()
        customer_dir = tmp_path / "carrier-alpha"
        customer_dir.mkdir()
        (customer_dir / "schema.yaml").write_text(original.to_yaml())
        loaded = CustomerSchema.load("carrier-alpha", tmp_path)
        assert loaded.customer_slug == original.customer_slug
        assert loaded.entity_hierarchy[0].entity == "device"
        assert loaded.sp_list_mappings == original.sp_list_mappings

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PipelineError) as ei:
            CustomerSchema.load("nonexistent", tmp_path)
        assert ei.value.code_id == "TSC-E001"

    def test_load_malformed_yaml_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "broken"
        d.mkdir()
        (d / "schema.yaml").write_text("not: a: valid: schema")
        with pytest.raises(PipelineError) as ei:
            CustomerSchema.load("broken", tmp_path)
        assert ei.value.code_id == "TSC-E001"

    def test_invalid_customer_slug_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CustomerSchema(
                customer_slug="has spaces",
                schema_version=1,
                entity_hierarchy=[],
            )


class TestEnums:
    """Value-set tests — locks the enum contract per MODULE.md Public surface."""

    def test_test_report_classification_values(self) -> None:
        assert _TRClassification.FINAL.value == "final"
        assert _TRClassification.INTERIM.value == "interim"

    def test_test_report_item_status_complete(self) -> None:
        # Per [D-011] FR-16
        assert {s.value for s in _TRItemStatus} == {
            "passed",
            "failed",
            "non-applicable",
            "waived",
            "not-started",
        }

    def test_delivery_state_11_values_per_fr7(self) -> None:
        """Per FR-7 — 11-value canonical enum (Not Started + 8 happy-path + Delayed + Blocked)."""
        assert len(DeliveryState) == 11
        assert {s.value for s in DeliveryState} == {
            "Not Started", "Open", "OutreachSent", "DocumentReceived",
            "OwnerClosed", "UnderPMReview", "ReadyForSubmission",
            "SubmittedToCustomer", "Closed", "Delayed", "Blocked",
        }

    def test_item_type_4_values_per_d053(self) -> None:
        """Per [D-053] impl note 2026-06-08 — 4-value collapsed enum."""
        assert len(ItemType) == 4
        assert {s.value for s in ItemType} == {
            "Confirmation",
            "TestTechWaiverReport",
            "ComplianceCertificationReleaseNotes",
            "Default",
        }

    def test_tracking_modality_5_values_per_d037(self) -> None:
        """Per [D-037] — 5 Ph-1 values; valid combinations require status + document capable."""
        assert len(TrackingModality) == 5
        assert {s.value for s in TrackingModality} == {
            "Email", "CorporateMessenger", "CorporatePLM",
            "NetworkSharedDrive", "CustomerJIRA",
        }

    def test_ingest_source_4_values_per_fr13(self) -> None:
        """Per FR-13 + [D-039] — recorded in document index."""
        assert len(IngestSource) == 4
        assert {s.value for s in IngestSource} == {
            "Email", "CorporatePLM", "NetworkSharedDrive", "SharePointUI",
        }

    def test_doc_type_5_values_per_d053(self) -> None:
        """Per [D-053] impl note 2026-06-08 — 5-value enum (test_report, tech_report,
        waiver, compliance_certification_release_notes, unresolved)."""
        assert len(DocType) == 5
        assert {s.value for s in DocType} == {
            "test_report",
            "tech_report",
            "waiver",
            "compliance_certification_release_notes",
            "unresolved",
        }

    def test_customer_delivery_modality_4_values_per_d054(self) -> None:
        """Per [D-054] — Ph-1/Ph-2 = Google Drive only; Ph-3+ values deferred."""
        assert len(CustomerDeliveryModality) == 4
        assert {s.value for s in CustomerDeliveryModality} == {
            "None", "Email", "CustomerTrackingSystem", "GoogleDrive",
        }

    def test_milestone_status_4_values(self) -> None:
        assert len(MilestoneStatus) == 4
        assert {s.value for s in MilestoneStatus} == {
            "Not Started", "In Progress", "Completed", "Delayed",
        }

    def test_rule_scope_3_values_per_fr30(self) -> None:
        assert len(RuleScope) == 3
        assert {s.value for s in RuleScope} == {"Global", "Customer", "Device"}

    def test_rule_action_type_24_values_per_fr29(self) -> None:
        """Per FR-29 — 18 Ph-1 + 6 Ph-2 = 24 total. Ph-2 values present in enum
        but rules using them are rejected at load time per customizations/rules/MODULE.md."""
        assert len(RuleActionType) == 24
        # Spot-check Ph-1 actions (FR-28 / FR-29 canonical names)
        ph1 = {
            "SendReminder", "Escalate", "UpdateState", "StartItemCollection",
            "SendInitialOutreach", "NotifyNewOwner", "TriggerParser",
            "TriggerAIReview", "QueueSubmission", "NotifyPM", "NotifyHildaOps",
            "InstantiateDefaultWorkItem", "MilestoneStorageCleanup",
            "HaltMilestonePolling", "FinalSweep", "ReassignDocumentToWorkItem",
            "PropagateTagsToActiveTrackers", "PMApproval",
        }
        ph2 = {
            "CancelOutstanding", "NotifyOwnerDocCountPending",
            "TriggerVersionSelection", "TriggerPLMCleanup", "TriggerODF",
            "SendOwnerRoutingQuery",
        }
        assert {s.value for s in RuleActionType} == ph1 | ph2

    def test_rule_trigger_type_15_values_per_fr28(self) -> None:
        """Per FR-28 — 13 Ph-1 + 2 Ph-2 = 15 total."""
        assert len(RuleTriggerType) == 15
        ph1 = {
            "ItemCreated", "ItemModified", "StateChange", "OwnerStatusConfirmed",
            "LastContactThreshold", "DeadlineProximity", "AttachmentReceived",
            "AIReviewResult", "PMApproval", "TrackerCreated", "MilestoneAllClosed",
            "CollectionPhaseClosureReached", "CredentialExpired",
        }
        ph2 = {"ItemDeleted", "UnroutedDocumentAccumulated"}
        assert {s.value for s in RuleTriggerType} == ph1 | ph2

    def test_rule_sub_trigger_type_3_values_per_fr28(self) -> None:
        """Sub-triggers under ItemModified per FR-28."""
        assert len(RuleSubTriggerType) == 3
        assert {s.value for s in RuleSubTriggerType} == {
            "OwnerReassigned", "DeadlineMoved", "TagsModified",
        }
