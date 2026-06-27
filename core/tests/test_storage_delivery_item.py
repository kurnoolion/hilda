"""Tests for core/src/storage/delivery_item_ops.py.

Mirrors the test_storage.py fixture pattern: per-test sqlite+aiosqlite engine
+ init_db. Validates the 6 CRUD/query ops + sync PostgresStorage wrapper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.src.storage.db import configure_engine, init_db
from core.src.storage.delivery_item_ops import (
    PostgresStorage,
    create_delivery_item,
    find_items_by_natural_key,
    get_delivery_item,
    list_default_workitem_for_milestone,
    list_items_for_milestone,
    update_delivery_item,
)
from core.src.template_schema import DeliveryItemBase


@pytest.fixture(autouse=True)
async def _fresh_db():
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()


def _mk_item(
    *,
    item_id: str,
    item_no: int = 5,
    customer_id: str = "MMK",
    device_id: str = "SM-S671U1",
    milestone_id: str = "P1",
    tg_name: str = "MNO-ETM",
    item_type: str = "test_tech_waiver_report",
    delivery_state: str = "Not Started",
    item_name: str = "Test item",
    sort_order: int = 5,
    path_id: str = "item_5",
    force_tracking_enabled: bool = True,
    **overrides,
) -> SimpleNamespace:
    """Build a duck-typed namespace with all required fields. SimpleNamespace
    suffices because create_delivery_item reads via getattr."""
    base = dict(
        item_id=item_id, item_no=item_no, customer_id=customer_id,
        device_id=device_id, milestone_id=milestone_id, tg_name=tg_name,
        item_type=item_type, delivery_state=delivery_state, item_name=item_name,
        sort_order=sort_order, path_id=path_id,
        force_tracking_enabled=force_tracking_enabled,
        tracking_modality=["Email"],
        last_updated=datetime.now(timezone.utc),
        doc_count=1, doc_count_received=0, reminder_count=0,
        no_customer_upload=False, milestone_gating=True, review_required=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# -----------------------------------------------------------------------------
# Async ops
# -----------------------------------------------------------------------------


async def test_create_delivery_item_round_trips():
    item = _mk_item(item_id="MMK-SM-S671U1-P1-5")
    new_id = await create_delivery_item(item)
    assert new_id == "MMK-SM-S671U1-P1-5"

    fetched = await get_delivery_item(new_id)
    assert fetched is not None
    assert fetched.item_no == 5
    assert fetched.item_type == "test_tech_waiver_report"
    assert fetched.delivery_state == "Not Started"
    assert fetched.tg_name == "MNO-ETM"
    assert fetched.tracking_modality == ["Email"]
    assert fetched.force_tracking_enabled is True


async def test_create_delivery_item_rejects_duplicate_id():
    from core.src.diagnostics.error_codes import PipelineError

    item = _mk_item(item_id="dup-id")
    await create_delivery_item(item)
    with pytest.raises(PipelineError) as exc:
        await create_delivery_item(_mk_item(item_id="dup-id", item_no=6))
    assert exc.value.code_id == "STR-E003"


async def test_get_delivery_item_returns_none_when_missing():
    # Use random-ish id to avoid bleed-through if engine state leaks across files
    result = await get_delivery_item("absolutely-does-not-exist-xyz-987654321")
    assert result is None


async def test_update_delivery_item_patches_fields():
    item = _mk_item(item_id="patch-test", reminder_count=0)
    await create_delivery_item(item)

    new_ts = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
    await update_delivery_item("patch-test", {
        "reminder_count": 3,
        "last_reminder_triggered_at": new_ts,
    })
    after = await get_delivery_item("patch-test")
    assert after.reminder_count == 3
    # SQLite normalizes tz info on round-trip; compare naive components
    assert after.last_reminder_triggered_at is not None
    assert after.last_reminder_triggered_at.replace(tzinfo=None) == new_ts.replace(tzinfo=None)


async def test_update_delivery_item_noop_on_missing_id():
    # Should not raise
    await update_delivery_item("ghost-id", {"reminder_count": 99})


async def test_list_items_for_milestone_returns_all_in_milestone():
    await create_delivery_item(_mk_item(item_id="a", milestone_id="P1", item_no=1, sort_order=1))
    await create_delivery_item(_mk_item(item_id="b", milestone_id="P1", item_no=2, sort_order=2))
    await create_delivery_item(_mk_item(item_id="c", milestone_id="P2", item_no=3, sort_order=3))

    items = await list_items_for_milestone("P1")
    ids = [i.item_id for i in items]
    assert ids == ["a", "b"]  # sorted by sort_order


async def test_list_items_for_milestone_filters_by_state():
    await create_delivery_item(_mk_item(item_id="open", delivery_state="Open"))
    await create_delivery_item(_mk_item(item_id="not-started", item_id_force_no="x",
                                         delivery_state="Not Started", sort_order=2, item_no=2))
    # Re-create with proper item_id (above had typo guard)
    items = await list_items_for_milestone("P1", states=["Open"])
    assert [i.item_id for i in items] == ["open"]


async def test_list_default_workitem_returns_only_default_for_milestone():
    await create_delivery_item(_mk_item(item_id="reg-1", item_no=1, item_type="test_tech_waiver_report"))
    await create_delivery_item(_mk_item(item_id="default-1", item_no=0, item_type="Default",
                                         tg_name="_unrouted", sort_order=99))
    result = await list_default_workitem_for_milestone("P1")
    assert result is not None
    assert result.item_id == "default-1"
    assert result.item_type == "Default"


async def test_list_default_workitem_returns_none_when_no_default():
    await create_delivery_item(_mk_item(item_id="r1", item_type="test_tech_waiver_report"))
    result = await list_default_workitem_for_milestone("P1")
    assert result is None


async def test_find_items_by_natural_key_returns_match():
    await create_delivery_item(_mk_item(
        item_id="match", customer_id="MMK", tg_name="MNO-ETM", item_no=5,
    ))
    matches = await find_items_by_natural_key(
        customer_id="MMK", tg_name="MNO-ETM", item_no=5,
    )
    assert len(matches) == 1
    assert matches[0].item_id == "match"


async def test_find_items_by_natural_key_returns_empty_when_no_match():
    await create_delivery_item(_mk_item(
        item_id="other", customer_id="MMK", tg_name="MNO-ETM", item_no=5,
    ))
    matches = await find_items_by_natural_key(
        customer_id="MMK", tg_name="MNO-ETM", item_no=99,
    )
    assert matches == []


# -----------------------------------------------------------------------------
# Sync PostgresStorage wrapper -- Protocol conformance
# -----------------------------------------------------------------------------


def test_postgres_storage_protocol_methods_exist():
    """Confirm PostgresStorage exposes the StorageWriter Protocol surface."""
    s = PostgresStorage()
    assert hasattr(s, "get_delivery_item")
    assert hasattr(s, "create_delivery_item")
    assert hasattr(s, "update_delivery_item")
    assert hasattr(s, "list_items_for_milestone")
    assert hasattr(s, "list_default_workitem_for_milestone")
    assert hasattr(s, "find_items_by_natural_key")
