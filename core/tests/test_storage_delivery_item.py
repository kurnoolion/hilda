"""Tests for core/src/storage/delivery_item_ops.py.

Mirrors the test_storage.py fixture pattern: per-test sqlite+aiosqlite engine
+ init_db. Validates the 6 CRUD/query ops + sync PostgresStorage wrapper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

# Suppress aiosqlite Connection __del__ teardown warnings -- these are benign
# SQLite-specific GC ordering quirks under pytest-asyncio + module-level engine
# singleton; production uses Postgres + asyncpg with proper connection pooling.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from core.src.storage.db import configure_engine, init_db
from core.src.storage.delivery_item_ops import (
    PostgresStorage,
    create_delivery_item,
    delete_milestone_cascade,
    find_items_by_natural_key,
    get_delivery_item,
    list_default_workitem_for_milestone,
    list_items_for_milestone,
    update_delivery_item,
)
from core.src.template_schema import DeliveryItemBase


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    # Per-test temp file (rather than :memory: which has cross-test bleed-through
    # under pytest-asyncio function scope when other test files share the
    # storage engine singleton).
    db_file = tmp_path / "test_di.db"
    engine = configure_engine(f"sqlite+aiosqlite:///{db_file}")
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
    item = _mk_item(item_id="round-trip-test-id-001")
    new_id = await create_delivery_item(item)
    assert new_id == "round-trip-test-id-001"

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
    # Use a UUID to guarantee no bleed-through if engine state leaks across files.
    import uuid as _uuid
    result = await get_delivery_item(f"missing-{_uuid.uuid4()}")
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
    assert hasattr(s, "delete_milestone_cascade")


# ============================================================================
# MDEL-1 (2026-07-28): delete_milestone_cascade — validates actual commit
# ============================================================================


async def test_delete_milestone_cascade_actually_persists():
    """First production run of apply_milestone_delete_task reported
    items_deleted=87 but Postgres still had all 87 rows. Root cause:
    session_scope() never commits -- delete() calls staged in the session
    were rolled back when the context exited. Fix: explicit
    await session.commit() at end of cascade.

    This test would have caught the bug: seed rows, run cascade, then
    RE-QUERY the DB and assert zero rows survive.
    """
    for i in range(3):
        await create_delivery_item(_mk_item(
            item_id=f"MMK-SM-S671U1-DRR-{i}",
            item_no=i, milestone_id="DRR",
        ))
    # Also seed a row in a DIFFERENT milestone -- must survive.
    await create_delivery_item(_mk_item(
        item_id="MMK-SM-S671U1-OTHER-1", milestone_id="OTHER-MS", item_no=99,
    ))

    # Sanity: all 4 exist before cascade
    assert await get_delivery_item("MMK-SM-S671U1-DRR-0") is not None
    assert await get_delivery_item("MMK-SM-S671U1-DRR-1") is not None
    assert await get_delivery_item("MMK-SM-S671U1-DRR-2") is not None
    assert await get_delivery_item("MMK-SM-S671U1-OTHER-1") is not None

    summary = await delete_milestone_cascade(
        customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
    )
    assert summary["items_deleted"] == 3

    # THE ACTUAL TEST — re-query after cascade
    assert await get_delivery_item("MMK-SM-S671U1-DRR-0") is None
    assert await get_delivery_item("MMK-SM-S671U1-DRR-1") is None
    assert await get_delivery_item("MMK-SM-S671U1-DRR-2") is None
    # Other milestone survives (scope isolation)
    assert await get_delivery_item("MMK-SM-S671U1-OTHER-1") is not None


async def test_delete_milestone_cascade_idempotent_on_empty_scope():
    # Nothing seeded -- cascade should return zeros without crashing.
    summary = await delete_milestone_cascade(
        customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
    )
    assert summary["items_deleted"] == 0
    assert summary["orphan_hashes"] == []


async def test_delete_milestone_cascade_device_scope_isolation():
    # Same customer + milestone_id, DIFFERENT devices -- cascade should
    # only touch the requested device.
    await create_delivery_item(_mk_item(
        item_id="MMK-SM-S671U1-DRR-1", milestone_id="DRR", device_id="SM-S671U1",
    ))
    await create_delivery_item(_mk_item(
        item_id="MMK-SM-M777U-DRR-1", milestone_id="DRR", device_id="SM-M777U",
        item_no=1,
    ))

    summary = await delete_milestone_cascade(
        customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
    )
    assert summary["items_deleted"] == 1
    assert await get_delivery_item("MMK-SM-S671U1-DRR-1") is None
    assert await get_delivery_item("MMK-SM-M777U-DRR-1") is not None  # different device survives


# ============================================================================
# SETUP-5 (2026-07-29): scope-level audit rows cleaned up too
# ============================================================================


async def test_delete_milestone_cascade_purges_scope_level_audits():
    """delivery_item_id=None audit rows (setup_complete_notified,
    tpm_notification summaries, etc.) survived MDEL because Step 6a filter
    WHERE delivery_item_id IN (item_ids) doesn't match NULL. Result: after
    milestone delete + re-setup, the tick's idempotency check found the
    OLD audit row (same customer+device+milestone in summary substring)
    and skipped the send -- TPM got no email for the fresh cycle.

    Fix (Step 6b): also delete communication_log rows where
    delivery_item_id IS NULL AND summary contains all three scope
    substrings. Other scopes' audits (different device or different
    milestone) survive.
    """
    from datetime import datetime, timezone
    import uuid, json
    from core.src.storage.db import CommunicationLogTable, session_scope
    from sqlalchemy import select

    # Seed one delivery_item so cascade has something to walk.
    await create_delivery_item(_mk_item(
        item_id="MMK-SM-S671U1-DRR-1", milestone_id="DRR",
    ))

    # Seed 3 scope-level audit rows (delivery_item_id=None):
    #   A. matches our target scope -- should get purged
    #   B. matches customer+milestone but DIFFERENT device -- survives
    #   C. matches customer+device but DIFFERENT milestone -- survives
    seed_rows = [
        ("A", "MMK", "SM-S671U1", "DRR"),        # target scope
        ("B", "MMK", "SM-M777U",  "DRR"),        # different device
        ("C", "MMK", "SM-S671U1", "OTHER-MS"),   # different milestone
    ]
    async with session_scope() as session:
        for tag, c, d, m in seed_rows:
            details = {"customer_id": c, "device_id": d, "milestone_id": m}
            summary = json.dumps(
                {"attribution": {}, "details": details},
                default=str, separators=(",", ":"),
            )
            session.add(CommunicationLogTable(
                log_id=f"tag-{tag}-{uuid.uuid4().hex[:8]}",
                channel="SharePoint", direction="Outbound",
                timestamp=datetime.now(timezone.utc),
                delivery_item_id=None,           # scope-level, not item-scoped
                summary=summary,
                action_type="setup_complete_notified",
                attachments=[],
            ))
        await session.commit()

    # Sanity: 3 audit rows exist.
    async with session_scope() as session:
        rows = (await session.execute(
            select(CommunicationLogTable).where(
                CommunicationLogTable.action_type == "setup_complete_notified"
            )
        )).scalars().all()
        assert len(rows) == 3

    summary = await delete_milestone_cascade(
        customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
    )
    # Row A purged (scope match); B + C survive (different scope).
    assert summary["audit_deleted"] >= 1   # at least the one target-scope row

    async with session_scope() as session:
        rows = (await session.execute(
            select(CommunicationLogTable).where(
                CommunicationLogTable.action_type == "setup_complete_notified"
            )
        )).scalars().all()
        surviving = {(json.loads(r.summary)["details"]["customer_id"],
                      json.loads(r.summary)["details"]["device_id"],
                      json.loads(r.summary)["details"]["milestone_id"])
                     for r in rows}
    # A gone, B + C survive.
    assert ("MMK", "SM-S671U1", "DRR")       not in surviving
    assert ("MMK", "SM-M777U",  "DRR")       in surviving
    assert ("MMK", "SM-S671U1", "OTHER-MS")  in surviving
