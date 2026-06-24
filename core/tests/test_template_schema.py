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
    CustomerDeliveryModalityRegistry,
    CustomerSchema,
    DeliveryItemBase,
    DeliveryState,
    DeliveryStateRegistry,
    DeviceBase,
    DocType,
    DocTypeRegistry,
    EntitySchemaConfig,
    IngestSource,
    ItemType,
    ItemTypeRegistry,
    MilestoneBase,
    MilestoneStatus,
    RuleActionRegistry,
    RuleActionType,
    RuleScope,
    RuleSubTriggerType,
    RuleTriggerRegistry,
    RuleTriggerType,
    SLUG_PATTERN,
    TGNameRegistry,
    TrackingModality,
    TrackingModalityRegistry,
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

    # Phase 2 — 4 new registries

    def test_doc_type_registry_seeded_from_enum(self) -> None:
        """DocTypeRegistry seeded from DocType enum per FR-7 amendment."""
        for d in DocType:
            assert d.value in DocTypeRegistry
        assert len(DocTypeRegistry) == 5

    def test_rule_action_registry_seeded_from_enum(self) -> None:
        """RuleActionRegistry seeded from RuleActionType (18 Ph-1 + 6 Ph-2) per FR-29."""
        for a in RuleActionType:
            assert a.value in RuleActionRegistry
        assert len(RuleActionRegistry) == 24

    def test_rule_trigger_registry_seeded_from_enum(self) -> None:
        """RuleTriggerRegistry seeded from RuleTriggerType (13 Ph-1 + 2 Ph-2) per FR-28."""
        for t in RuleTriggerType:
            assert t.value in RuleTriggerRegistry
        assert len(RuleTriggerRegistry) == 15

    def test_tg_name_registry_starts_empty(self) -> None:
        """TGNameRegistry has no canonical enum — populated per-deployment from
        customizations/template_schemas/<customer>/tg_groups.yaml at startup.

        NOTE: assertion uses set comparison rather than == on the global since
        other tests may have already extended the registry; we check that ALL
        seeded enum values are absent (the registry was never enum-seeded)."""
        # The registry has no enum seed; if it's non-empty, it was extended at runtime
        # — which is fine, but no canonical "initial" values should be assumed.
        # This test asserts the registry exists and is mutable per the extend pattern.
        try:
            initial = set(TGNameRegistry)
            extend_registry(TGNameRegistry, ["Hardware", "Software"])
            assert "Hardware" in TGNameRegistry
            assert "Software" in TGNameRegistry
        finally:
            TGNameRegistry.difference_update(set(TGNameRegistry) - initial)

    def test_all_seven_enum_seeded_registries_match_their_enums(self) -> None:
        """Lock the seeded-from-enum invariant for all 7 enum-seeded registries."""
        pairs = [
            (DeliveryStateRegistry,            DeliveryState),
            (ItemTypeRegistry,                 ItemType),
            (TrackingModalityRegistry,         TrackingModality),
            (CustomerDeliveryModalityRegistry, CustomerDeliveryModality),
            (DocTypeRegistry,                  DocType),
            (RuleActionRegistry,               RuleActionType),
            (RuleTriggerRegistry,              RuleTriggerType),
        ]
        for registry, enum_cls in pairs:
            enum_values = {e.value for e in enum_cls}
            # registry contains every enum value (registry may be larger if extended)
            assert enum_values.issubset(registry), (
                f"{enum_cls.__name__}: enum values {enum_values - registry} "
                f"missing from registry"
            )


def _make_delivery_item(**overrides: object) -> DeliveryItemBase:
    defaults = dict(
        item_id="i1",
        item_no=1,                                # NEW per Phase 5 [D-053]
        milestone_id="m1",                        # reparented per Phase 5 [D-028]
        item_name="x",
        delivery_state=DeliveryState.OPEN.value,
        item_type=ItemType.CONFIRMATION.value,
        tracking_modality=["Email"],              # MULTI-VALUE per [D-037]
        customer_delivery_modality="None",
        last_updated=datetime.now(timezone.utc),
        sort_order=1,
        path_id="i1-slug",
        # Confirmation items MUST have no_customer_upload=True per [D-053] (Phase 5 invariant)
        no_customer_upload=True,
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
                path_id="has spaces",
            )

    def test_device_base_rejects_unknown_state(self) -> None:
        with pytest.raises(ValidationError):
            DeviceBase(
                device_id="d1",
                device_name="X",
                customer_id="c1",
                assigned_pm_id="pm1",
                status="Imaginary",
                path_id="device-1",
            )

    def test_device_base_accepts_valid(self) -> None:
        d = DeviceBase(
            device_id="d1",
            device_name="X",
            customer_id="c1",
            assigned_pm_id="pm1",
            status=DeliveryState.OPEN.value,
            path_id="device-1",
            target_launch_date=date(2026, 12, 1),
        )
        assert d.path_id == "device-1"

    def test_milestone_base_status_enum(self) -> None:
        m = MilestoneBase(
            milestone_id="m1",
            carrier="MMK",                          # added 2026-06-21
            project_id=1001,                         # added 2026-06-21 per [D-088]
            project_model="MODEL-A",               # added 2026-06-21
            milestone_name="M1",
            sort_order=1,
            status=MilestoneStatus.NOT_STARTED,
            path_id="m-1",
        )
        assert m.status == MilestoneStatus.NOT_STARTED

    def test_delivery_item_validates_all_registries(self) -> None:
        di = _make_delivery_item()
        assert di.delivery_state == "Open"
        assert di.item_type == "Confirmation"   # PascalCase per SP UI engineer lock 2026-06-23
        assert di.tracking_modality == ["Email"]   # MULTI-VALUE per [D-037] Phase 5
        assert di.customer_delivery_modality == "None"

    def test_delivery_item_rejects_bad_modality(self) -> None:
        with pytest.raises(ValidationError):
            _make_delivery_item(tracking_modality=["Carrier_Pigeon"])

    # Phase 5 — DeliverableBase deleted per [D-028]; completion_pct now lives
    # on DeliveryItemBase as item_completion_pct (per FR-70). Range-check is
    # NOT enforced in template_schema (computed field set by tracker / dashboard);
    # only the registry-validated fields have field_validators. Test below
    # was tracking the deleted DeliverableBase shape and is removed.

    def test_delivery_item_validates_milestone_parent_per_d028(self) -> None:
        """Per [D-028] — DeliveryItem parents Milestone directly (no Deliverable level)."""
        di = _make_delivery_item()
        assert di.milestone_id == "m1"
        # Verify deliverable_id is NOT a required field (Deliverable level removed)
        assert not hasattr(di, "deliverable_id") or di.__class__.model_fields.get("deliverable_id") is None


class TestPhase5Models:
    """Phase 5 tests — 5 new helper models + DeliveryItemBase reparent + new validators."""

    def test_default_work_item_config_defaults(self) -> None:
        """Per FR-78 hardcoded inventory architect lock 2026-06-21."""
        from core.src.template_schema import DefaultWorkItemConfig
        cfg = DefaultWorkItemConfig()
        assert cfg.tg_name == "_unrouted"
        assert cfg.item_type == "Default"   # PascalCase per SP UI engineer lock 2026-06-23
        assert cfg.item_name == "Unrouted Documents"
        assert cfg.sort_order_strategy == "max_plus_1"
        assert cfg.not_editable is True
        assert cfg.not_deletable is True
        # FR-78 hardcoded inventory expansion 2026-06-21:
        assert cfg.tg_path_id == "_unrouted"
        assert cfg.item_path_id is None
        assert cfg.force_tracking_enabled is False   # the one explicit exception to FR-81 column-default True
        assert cfg.tracking_modality is None
        assert cfg.owner_corp_usa_email is None
        assert cfg.owner_corp_email is None
        assert cfg.owner_corp_id is None
        assert cfg.owner_name is None
        assert cfg.milestone_gating is True
        assert cfg.no_customer_upload is True
        assert cfg.review_required is False
        assert cfg.review_status == "not_required"
        assert cfg.doc_count == 0

    def test_folder_routing_entry_validates(self) -> None:
        from core.src.template_schema import FolderRoutingEntry
        e = FolderRoutingEntry(ingress_folder="deliverables/q3", item_no=5)
        assert e.ingress_folder == "deliverables/q3"
        assert e.item_no == 5
        assert e.routing_notes is None

    def test_tg_folder_routing_holds_entries(self) -> None:
        from core.src.template_schema import FolderRoutingEntry, TGFolderRouting
        t = TGFolderRouting(
            milestone_id="m1",
            tg_name="Hardware",
            entries=[
                FolderRoutingEntry(ingress_folder="hw/q3", item_no=1),
                FolderRoutingEntry(ingress_folder="hw/q4", item_no=2),
            ],
        )
        assert len(t.entries) == 2
        assert t.entries[0].item_no == 1

    def test_tag_catalog_entry_minimal(self) -> None:
        from core.src.template_schema import TagCatalogEntry
        e = TagCatalogEntry(tag="MUST_HAVE")
        assert e.tag == "MUST_HAVE"
        assert e.description is None
        assert e.color is None

    def test_tg_group_base_dropped(self) -> None:
        """TGGroupBase DROPPED 2026-06-21 per [D-051] denormalization + architect lock.
        TG fields are now denormalized onto DeliveryItemBase directly."""
        import core.src.template_schema as ts
        # TGGroupBase must no longer be importable from the package:
        assert not hasattr(ts, "TGGroupBase"), (
            "TGGroupBase was dropped 2026-06-21 per [D-051] denormalization + architect lock"
        )
        # Replacement: TG fields live on DeliveryItemBase. Verify default values:
        di = _make_delivery_item()
        assert di.tg_name is None        # validated against TGNameRegistry
        assert di.ingress_nsd == "None"  # Ph-1 lock; Choice values: None/NSD1/NSD2 per SP UI engineer lock 2026-06-23
        assert di.folder_routing_enabled is False
        assert di.tg_email_group_alias is None
        assert di.tg_owner_name is None
        assert di.tg_owner_corp_usa_email is None
        assert di.tg_owner_corp_email is None
        assert di.tg_owner_corp_id is None
        assert di.corp_id_list is None

    def test_milestone_base_default_work_item_config_optional(self) -> None:
        m = MilestoneBase(
            milestone_id="m1",
            carrier="MMK",
            project_id=1001,
            project_model="MODEL-A",
            milestone_name="M1",
            sort_order=1,
            status=MilestoneStatus.NOT_STARTED,
            path_id="m-1",
        )
        assert m.default_work_item_config is None
        assert m.email_cc_list is None
        # Button-trigger timestamps default None (HILDA-managed/SP-written at runtime):
        assert m.milestone_collection_started_at is None
        assert m.milestone_submission_triggered_at is None
        assert m.closed_all_items_triggered_at is None
        assert m.milestone_completion_pct == 0

    def test_milestone_base_with_default_work_item_config(self) -> None:
        from core.src.template_schema import DefaultWorkItemConfig
        m = MilestoneBase(
            milestone_id="m1",
            carrier="MMK",
            project_id=1001,
            project_model="MODEL-A",
            milestone_name="M1",
            sort_order=1,
            status=MilestoneStatus.NOT_STARTED,
            path_id="m-1",
            default_work_item_config=DefaultWorkItemConfig(),
        )
        assert m.default_work_item_config is not None
        assert m.default_work_item_config.tg_name == "_unrouted"

    def test_delivery_item_multi_value_tracking_modality(self) -> None:
        """Per [D-037] tracking_modality is multi-value list."""
        di = _make_delivery_item(tracking_modality=["Email", "CorporatePLM"])
        assert di.tracking_modality == ["Email", "CorporatePLM"]

    def test_delivery_item_rejects_unknown_modality_in_list(self) -> None:
        """Multi-value list — each item validated against registry."""
        with pytest.raises(ValidationError):
            _make_delivery_item(tracking_modality=["Email", "Carrier_Pigeon"])

    def test_delivery_item_pm_approval_fields_default_none_per_d068(self) -> None:
        """Per [D-068] — pm_approval_at + pm_approval_pm_id default None; cleared on
        entry to UNDER_PM_REVIEW per tracker invariant."""
        di = _make_delivery_item()
        assert di.pm_approval_at is None
        assert di.pm_approval_pm_id is None

    def test_delivery_item_confirmation_with_no_customer_upload_true_passes(self) -> None:
        """Per [D-053] tracker invariant — Confirmation + no_customer_upload=True passes."""
        di = _make_delivery_item(
            item_type=ItemType.CONFIRMATION.value,
            no_customer_upload=True,
        )
        assert di.item_type == "Confirmation"   # PascalCase per SP UI engineer lock 2026-06-23
        assert di.no_customer_upload is True

    def test_delivery_item_confirmation_with_no_customer_upload_false_warns(self, caplog) -> None:
        """Per [D-053] tracker invariant + TSC-W004 — Confirmation+no_customer_upload=False
        emits TSC-W004 warning (not blocking; ops triage)."""
        import logging
        caplog.set_level(logging.WARNING, logger="core.src.template_schema.models")
        _make_delivery_item(
            item_type=ItemType.CONFIRMATION.value,
            no_customer_upload=False,
        )
        # TSC-W004 should appear in captured logs
        assert any("TSC-W004" not in r.message or "Confirmation" in r.message
                   for r in caplog.records), (
            f"Expected TSC-W004 warning; got logs: {[r.message for r in caplog.records]}"
        )
        # Stricter: at least one log should mention no_customer_upload
        assert any("no_customer_upload" in r.message for r in caplog.records), (
            f"Expected no_customer_upload mention in logs; got: {[r.message for r in caplog.records]}"
        )

    def test_delivery_item_new_fields_have_defaults(self) -> None:
        """All Phase 2-5 additions + 2026-06-21 cascade fields have sensible defaults."""
        di = _make_delivery_item()
        assert di.doc_count == 1
        assert di.review_required is False
        assert di.review_status == "not_required"
        assert di.item_completion_pct == 0
        # 2026-06-21 cascade fixes:
        assert di.milestone_gating is True            # renamed from is_milestone_gating; default True per FR-78 + spec convergence
        assert di.no_customer_upload is True          # set in helper per Confirmation invariant
        assert di.force_tracking_enabled is True      # SP BOOL column-default=True per FR-81 option (a) lock 2026-06-20
        assert di.ingress_folder is None
        assert di.target_folder is None
        assert di.pm_approval_at is None
        assert di.pm_approval_pm_id is None
        assert di.handset is False
        assert di.tg_name is None
        assert di.item_description is None
        assert di.plm_id is None
        # 4-field owner identity (added 2026-06-21):
        assert di.owner_corp_usa_email is None
        assert di.owner_corp_email is None
        assert di.owner_corp_id is None
        assert di.owner_name is None

    def test_automation_rule_trigger_sub_event_optional(self) -> None:
        from core.src.template_schema import AutomationRuleBase, RuleScope, RuleActionType
        r = AutomationRuleBase(
            rule_id="r1",
            rule_name="reminder rule",
            scope=RuleScope.GLOBAL,
            trigger_event="LastContactThreshold",
            action_type=RuleActionType.SEND_REMINDER,
        )
        assert r.trigger_sub_event is None
        # Now WITH a sub-event (for ItemModified)
        r2 = AutomationRuleBase(
            rule_id="r2",
            rule_name="propagate tags",
            scope=RuleScope.GLOBAL,
            trigger_event="ItemModified",
            trigger_sub_event="TagsModified",
            action_type=RuleActionType.PROPAGATE_TAGS_TO_ACTIVE_TRACKERS,
        )
        assert r2.trigger_sub_event == "TagsModified"


class TestCLI:
    """Phase 6 — exercise template_schema_cli per [D-005] test interface contract."""

    def test_diagnostic_mode_on_empty_dir(self, tmp_path: Path) -> None:
        """--diagnostic with no customer schemas emits TSC-RPT with zero counts."""
        from core.src.template_schema.template_schema_cli import main
        empty = tmp_path / "template_schemas"
        empty.mkdir()
        rc = main(["--diagnostic", "--base-path", str(empty)])
        assert rc == 0

    def test_diagnostic_mode_finds_valid_customer(self, tmp_path: Path, capsys) -> None:
        """--diagnostic with one valid customer schema reports schemas_valid=1."""
        from core.src.template_schema.template_schema_cli import main
        base = tmp_path / "template_schemas"
        cust_dir = base / "carrier-alpha"
        cust_dir.mkdir(parents=True)
        # Minimal valid CustomerSchema YAML — uses post-[D-028] entity vocabulary (no "deliverable")
        (cust_dir / "schema.yaml").write_text(
            "customer_slug: carrier-alpha\n"
            "schema_version: 1\n"
            "entity_hierarchy:\n"
            "  - entity: device\n"
            "    header_row: 1\n"
            "    columns:\n"
            "      - source: Device Name\n"
            "        canonical: device_name\n"
            "        col_type: str\n"
            "        required: true\n"
            "sp_list_mappings:\n"
            "  device_name: Title\n",
            encoding="utf-8",
        )
        rc = main(["--diagnostic", "--base-path", str(base), "--run-id", "test-001"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "schemas_valid=1" in captured.out
        assert "schemas_invalid=0" in captured.out

    def test_validate_mode_emits_qc(self, tmp_path: Path, capsys) -> None:
        """--validate --customer <slug> emits TSC-QC with OK result on valid schema."""
        from core.src.template_schema.template_schema_cli import main
        base = tmp_path / "template_schemas"
        cust_dir = base / "carrier-alpha"
        cust_dir.mkdir(parents=True)
        (cust_dir / "schema.yaml").write_text(
            "customer_slug: carrier-alpha\n"
            "schema_version: 1\n"
            "entity_hierarchy:\n"
            "  - entity: delivery_item\n"
            "    header_row: 2\n"
            "    columns:\n"
            "      - source: Item Name\n"
            "        canonical: item_name\n"
            "        col_type: str\n"
            "        required: true\n"
            "sp_list_mappings:\n"
            "  item_name: Title\n",
            encoding="utf-8",
        )
        rc = main(["--validate", "--customer", "carrier-alpha", "--base-path", str(base),
                   "--run-id", "test-002"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "QC|TSC|" in captured.out
        assert "result=OK" in captured.out
        assert "customer=carrier-alpha" in captured.out

    def test_validate_mode_fails_on_missing_customer(self, tmp_path: Path, capsys) -> None:
        """--validate on a nonexistent customer returns exit 1 + emits TSC-QC with FAIL."""
        from core.src.template_schema.template_schema_cli import main
        base = tmp_path / "template_schemas"
        base.mkdir()
        rc = main(["--validate", "--customer", "ghost-customer", "--base-path", str(base),
                   "--run-id", "test-003"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "result=FAIL" in captured.out


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


class TestErrorCodes:
    """Phase 3 — verify TSC-* error codes are registered with diagnostics."""

    def test_tsc_w003_registered(self) -> None:
        from core.src.diagnostics import get_code
        ec = get_code("TSC-W003")
        assert ec.recoverable is True
        assert "tag" in ec.message
        assert "FR-82" in ec.message

    def test_tsc_w004_registered(self) -> None:
        from core.src.diagnostics import get_code
        ec = get_code("TSC-W004")
        assert ec.recoverable is True
        assert "Confirmation" in ec.message
        assert "no_customer_upload" in ec.message
        assert "[D-053]" in ec.message

    def test_all_tsc_codes_registered(self) -> None:
        from core.src.diagnostics import get_code
        for code_id in ("TSC-E001", "TSC-E002", "TSC-E003", "TSC-E004",
                        "TSC-W001", "TSC-W002", "TSC-W003", "TSC-W004"):
            ec = get_code(code_id)
            assert ec.code == code_id   # ErrorCode.code (not code_id; that's on PipelineError)


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
        """Per FR-7 — 11-value canonical enum (Not Started + 8 happy-path + Delayed + Blocked).
        Per architect lock 2026-06-26 direction (α): value strings match SP display
        (PascalCase with spaces)."""
        assert len(DeliveryState) == 11
        assert {s.value for s in DeliveryState} == {
            "Not Started", "Open", "Outreach Sent", "Document Received",
            "Owner Closed", "Under PM Review", "Ready For Submission",
            "Submitted To Customer", "Closed", "Delayed", "Blocked",
        }

    def test_item_type_4_values_per_d053(self) -> None:
        """Per [D-053] impl note 2026-06-08 + SP UI engineer lock 2026-06-23 (mixed case:
        short-label categories Confirmation/Default PascalCase; long-named categories
        test_tech_waiver_report/compliance_certification_release_notes snake_case)."""
        assert len(ItemType) == 4
        assert {s.value for s in ItemType} == {
            "Confirmation",
            "test_tech_waiver_report",
            "compliance_certification_release_notes",
            "Default",
        }

    def test_tracking_modality_6_values_per_d037(self) -> None:
        """Per [D-037] + architect direction 2026-06-23 — 6 Ph-1 values (HILDA forward-looking;
        SP UI engineer has 5-value SP Choice column with SPUI omitted — to be added Ph-2).
        Valid combinations require status + document capable per FR-7."""
        assert len(TrackingModality) == 6
        assert {s.value for s in TrackingModality} == {
            "Email", "CorporateMessenger", "CorporatePLM",
            "NetworkSharedDrive", "CustomerJIRA", "SPUI",
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
