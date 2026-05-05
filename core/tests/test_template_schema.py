"""Unit tests for core.src.template_schema."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.src.diagnostics import PipelineError
from core.src.template_schema import (
    ColumnMapping,
    CustomerSchema,
    DeliveryItemBase,
    DeliveryState,
    DeliveryStateRegistry,
    DeviceBase,
    EntitySchemaConfig,
    ItemType,
    MilestoneBase,
    MilestoneStatus,
    SLUG_PATTERN,
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
        item_type=ItemType.BINARY.value,
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
        assert di.item_type == "Binary"
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
