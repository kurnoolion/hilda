"""MTR-1 tests (2026-08-27) -- refresh_manual_triage_after_view_save behavior.

Exercises the aggregate-across-item + Postgres-write + SP-writeback + audit paths
using in-memory sqlite for storage + MagicMock deps for SP writeback + audit.

Scenarios covered:
  * Owner resend after TPM edit -> needs_merge=True -> flag set on item + SP write.
  * TPM edits back to become current -> needs_merge=False -> flag cleared + SP write.
  * No files in TG -> no-op (no writes, empty stats).
  * All items already have correct flag -> no flips (items_recomputed>0 but items_flipped=0).
  * SP writeback failure gracefully counted (sp_writeback_failed) and does not raise.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis.aioredis
import pytest

from core.src.storage import configure_engine, init_db, set_redis_client
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.db import (
    DocumentIndexTable, DocumentItemAssociationTable,
    DocumentVersionTable, session_scope,
)
from core.src.storage.document_view_ops import save_view_document
from core.src.storage.models import NSDPathType
from core.src.workflow_engine.tasks.manual_triage import (
    refresh_manual_triage_after_view_save,
)


pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)


NOW = datetime.now(timezone.utc)
CUSTOMER = "MMK"
DEVICE = "SM-S671U1"
MILESTONE = "P1"
TG = "HW PL"
ITEM_ID = f"{CUSTOMER}-{DEVICE}-{MILESTONE}-42"


@pytest.fixture(autouse=True)
async def env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    client = fakeredis.aioredis.FakeRedis()
    set_redis_client(client)
    yield
    await client.aclose()
    await engine.dispose()
    set_redis_client(None)
    set_storage_config(None)


async def _seed_item_row(*, manual_triage_required: bool = False) -> SimpleNamespace:
    """Return a SimpleNamespace matching storage.get_delivery_item's return
    shape (subset used by MTR-1). No real DeliveryItemTable insert is needed --
    deps.storage.get_delivery_item is mocked to return this namespace directly."""
    return SimpleNamespace(
        item_id=ITEM_ID, customer_id=CUSTOMER, device_id=DEVICE,
        milestone_id=MILESTONE, tg_name=TG, item_no=42, sp_id=1234,
        manual_triage_required=manual_triage_required,
    )


async def _seed_doc(*, filename: str, saved_by_sequence: list[str]) -> str:
    """Write N versions of a doc through save_view_document; also seed an index
    row + a classified assoc keyed on the FIRST version's sha256 (mirrors real
    ingest wiring)."""
    file_hash = ""
    for i, saved_by in enumerate(saved_by_sequence):
        content = f"v{i+1}-{saved_by}".encode()
        row = await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
            relative_parts=(filename,),
            content=content, saved_by=saved_by,
        )
        if i == 0:
            file_hash = row.sha256
    # Seed document_index + document_item_association for the FIRST version's hash
    # (this is what routing writes at ingest). Later versions don't get new
    # assoc rows -- that's the design intent user described.
    async with session_scope() as session:
        session.add(DocumentIndexTable(
            file_hash=file_hash,
            milestone_id=MILESTONE,
            customer_id=CUSTOMER,
            device_id=DEVICE,
            doc_type="test_report",
            doc_id_slug="test-slug",
            rev_number=1,
            ingest_source="Email",
            original_filename=filename,
            inferred_tg_name=TG,
            routing_resolution="SubstringMatch",
            ingested_at=NOW,
        ))
        session.add(DocumentItemAssociationTable(
            file_hash=file_hash,
            delivery_item_id=ITEM_ID,
            milestone_id=MILESTONE,
            local_nsd_path=f"internal/.../{filename}",
            nsd_path_type=NSDPathType.CLASSIFIED.value,
            owner_corp_id="",
            associated_at=NOW,
        ))
        await session.commit()
    return file_hash


def _mk_deps(item_ns, update_fn_effect=None, sp_writer_fails=False):
    """Build a minimal deps mock. storage.get_delivery_item returns item_ns;
    storage.update_delivery_item mutates item_ns.manual_triage_required unless
    update_fn_effect provided (e.g., to raise)."""
    storage = MagicMock()
    storage.get_delivery_item.return_value = item_ns

    def _update(item_id: str, updates: dict):
        if update_fn_effect is not None:
            update_fn_effect(item_id, updates)
        if "manual_triage_required" in updates:
            item_ns.manual_triage_required = updates["manual_triage_required"]
    storage.update_delivery_item.side_effect = _update

    sp_writer = MagicMock()
    if sp_writer_fails:
        sp_writer.update_item.side_effect = RuntimeError("boom")
    audit = MagicMock()
    return SimpleNamespace(storage=storage, sp_writer=sp_writer, audit=audit)


class TestManualTriageRefresh:

    async def test_needs_merge_true_sets_flag_and_writes_sp(self):
        """Owner resends after TPM edit -> current is owner (auto), prior TPM ->
        needs_merge=True. Item's manual_triage_required flips False -> True + SP write."""
        item = await _seed_item_row(manual_triage_required=False)
        # v1 owner (auto), v2 TPM, v3 owner resend -> current is auto, prior TPM -> needs_merge=True
        await _seed_doc(filename="a.pdf",
                        saved_by_sequence=["auto", "tpm@corp", "auto"])
        deps = _mk_deps(item)

        stats = await refresh_manual_triage_after_view_save(
            deps, customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
        )

        assert stats["items_recomputed"] == 1
        assert stats["items_flipped"] == 1
        assert stats["sp_writeback_ok"] == 1
        assert item.manual_triage_required is True
        # SP writeback used the item's sp_id (avoids natural-key round-trip)
        deps.sp_writer.update_item.assert_called_once()
        call = deps.sp_writer.update_item.call_args.kwargs
        assert call["item_id"] == "1234"
        assert call["canonical_fields"] == {"manual_triage_required": True}

    async def test_tpm_last_no_merge_clears_flag(self):
        """v1 owner, v2 TPM edit -> TPM is current -> needs_merge=False.
        If flag was previously True, it should clear + push False to SP."""
        item = await _seed_item_row(manual_triage_required=True)
        await _seed_doc(filename="b.pdf",
                        saved_by_sequence=["auto", "tpm@corp"])
        deps = _mk_deps(item)

        stats = await refresh_manual_triage_after_view_save(
            deps, customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
        )
        assert stats["items_flipped"] == 1
        assert item.manual_triage_required is False
        call = deps.sp_writer.update_item.call_args.kwargs
        assert call["canonical_fields"] == {"manual_triage_required": False}

    async def test_no_files_in_tg_noop(self):
        item = await _seed_item_row(manual_triage_required=False)
        deps = _mk_deps(item)
        stats = await refresh_manual_triage_after_view_save(
            deps, customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
        )
        assert stats == {
            "items_recomputed": 0, "items_flipped": 0,
            "sp_writeback_ok": 0, "sp_writeback_failed": 0,
        }
        deps.sp_writer.update_item.assert_not_called()

    async def test_already_correct_no_flip(self):
        """Flag matches target -> no Postgres update, no SP write, no audit."""
        item = await _seed_item_row(manual_triage_required=False)
        # v1 owner + v2 TPM edit -> needs_merge=False -> matches item.manual_triage_required=False
        await _seed_doc(filename="c.pdf",
                        saved_by_sequence=["auto", "tpm@corp"])
        deps = _mk_deps(item)
        stats = await refresh_manual_triage_after_view_save(
            deps, customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
        )
        assert stats["items_recomputed"] == 1
        assert stats["items_flipped"] == 0
        deps.storage.update_delivery_item.assert_not_called()
        deps.sp_writer.update_item.assert_not_called()

    async def test_sp_writeback_failure_counted_not_raised(self):
        """SP writeback throws -> stats.sp_writeback_failed++ + Postgres write
        still stands + no exception propagates."""
        item = await _seed_item_row(manual_triage_required=False)
        await _seed_doc(filename="d.pdf",
                        saved_by_sequence=["auto", "tpm@corp", "auto"])
        deps = _mk_deps(item, sp_writer_fails=True)
        stats = await refresh_manual_triage_after_view_save(
            deps, customer_id=CUSTOMER, device_id=DEVICE,
            milestone_id=MILESTONE, tg_name=TG,
        )
        assert stats["items_flipped"] == 1
        assert stats["sp_writeback_ok"] == 0
        assert stats["sp_writeback_failed"] == 1
        assert item.manual_triage_required is True  # Postgres write landed
