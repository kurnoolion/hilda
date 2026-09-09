"""UPLOAD-MANIFEST-1: milestone-wide submission preview.

Answers "where will these files land on Google Drive" BEFORE the TPM clicks
Submit in the SP UI. The whole requirement is fidelity: included rows come
from the same resolvers submit_to_carrier calls, and destinations from
storage.upload_plan, so the preview cannot disagree with the delivery.

The excluded rows matter as much as the included ones -- a file missing from
the submission with no explanation is the failure DOCTYPE-MISALIGN-UI-1 was
about, one milestone wider.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import (
    build_milestone_manifest,
    configure_engine,
    init_db,
    list_files_in_tg,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.models import NSDPathType

CUST, DEV, MS = "MMK", "SM-S671U1", "P1"
TG = "HW PL"
BATTERY = "Feature Test Results/Battery"


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


async def _add_item(item_no, *, target_folder=BATTERY, no_upload=False,
                    state="ReadyForSubmission", tg=TG,
                    item_type="test_tech_waiver_report"):
    from core.src.storage.db import DeliveryItemTable, session_scope
    now = datetime.now(timezone.utc)
    item_id = f"{CUST}-{DEV}-{MS}-{item_no}"
    async with session_scope() as s:
        s.add(DeliveryItemTable(
            item_id=item_id, customer_id=CUST, device_id=DEV, milestone_id=MS,
            item_no=item_no, item_type=item_type, item_name=f"Item {item_no}",
            tg_name=tg, delivery_state=state, target_folder=target_folder,
            no_customer_upload=no_upload, last_updated=now,
            sort_order=item_no, path_id=f"p{item_no}",
        ))
        await s.commit()
    return item_id


async def _add_doc(item_id, rel_parts, *, doc_type="test_report",
                   from_zip=False, nsd_path_type=None, tg=TG, slug=None):
    from core.src.storage.db import (
        DocumentIndexTable, DocumentItemAssociationTable, session_scope,
    )
    nsd_path_type = nsd_path_type or NSDPathType.CLASSIFIED.value
    await save_view_document(
        customer_id=CUST, device_id=DEV, milestone_id=MS, tg_name=tg,
        relative_parts=rel_parts,
        # Unique bytes per file: document_index.file_hash is the sole PK.
        content=("b:" + item_id + "/".join(rel_parts)).encode(),
        saved_by="auto",
    )
    rel_path = f"view/{CUST}/{DEV}/{MS}/{tg}/" + "/".join(rel_parts)
    fh = next(f.file_hash for f in await list_files_in_tg(
        customer_id=CUST, device_id=DEV, milestone_id=MS, tg_name=tg)
        if f.view_relative_path == rel_path)
    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        s.add(DocumentIndexTable(
            file_hash=fh, milestone_id=MS, doc_type=doc_type,
            doc_id_slug=slug or ("slug_" + rel_parts[-1]), rev_number=1,
            ingest_source="NetworkSharedDrive", original_filename=rel_parts[-1],
            inferred_tg_name=tg, routing_resolution="SubstringMatch",
            from_zip=from_zip, ingested_at=now,
        ))
        s.add(DocumentItemAssociationTable(
            file_hash=fh, delivery_item_id=item_id, milestone_id=MS,
            local_nsd_path="internal/x/" + "/".join(rel_parts),
            nsd_path_type=nsd_path_type, owner_corp_id="", associated_at=now,
        ))
        await s.commit()
    return fh


def _rows(manifest, item_no):
    return next(i for i in manifest.items if i.item_no == item_no).rows


class TestIncludedFiles:
    async def test_destination_matches_the_uploader_rule(self):
        item = await _add_item(10)
        await _add_doc(item, ("battery.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        (row,) = _rows(m, 10)
        assert row.excluded_reason == ""
        assert row.carrier_destination == BATTERY + "/battery.pdf"
        assert m.total_included == 1

    async def test_zip_subfolder_is_preserved(self):
        item = await _add_item(10)
        await _add_doc(item, ("DOU", "battery.pdf"), from_zip=True)
        (row,) = _rows(m := await build_milestone_manifest(CUST, DEV, MS), 10)
        assert row.carrier_destination == BATTERY + "/DOU/battery.pdf"
        assert m.total_included == 1

    async def test_nsd_folder_is_flattened(self):
        item = await _add_item(10)
        await _add_doc(item, ("2. DMDform (Done)", "r.pdf"), from_zip=False)
        (row,) = _rows(await build_milestone_manifest(CUST, DEV, MS), 10)
        assert row.carrier_destination == BATTERY + "/r.pdf"
        assert "DMDform" not in row.carrier_destination


class TestExcludedFiles:
    async def test_waiver_is_listed_with_a_reason_not_omitted(self):
        item = await _add_item(10)
        await _add_doc(item, ("bt_waiver.pdf",), doc_type="waiver")
        m = await build_milestone_manifest(CUST, DEV, MS)
        rows = _rows(m, 10)
        assert len(rows) == 1
        assert rows[0].carrier_destination == ""
        assert "waiver" in rows[0].excluded_reason
        assert m.total_included == 0 and m.total_excluded == 1

    async def test_misaligned_doc_is_listed_as_staged(self):
        item = await _add_item(10)
        await _add_doc(item, ("odd.pdf",),
                       nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value)
        rows = _rows(await build_milestone_manifest(CUST, DEV, MS), 10)
        assert rows[0].carrier_destination == ""
        assert "staged" in rows[0].excluded_reason

    async def test_no_customer_upload_item_excludes_its_files(self):
        item = await _add_item(10, no_upload=True)
        await _add_doc(item, ("x.pdf",))
        rows = _rows(await build_milestone_manifest(CUST, DEV, MS), 10)
        assert rows[0].carrier_destination == ""
        assert "no_customer_upload" in rows[0].excluded_reason

    async def test_item_without_target_folder_excludes_its_files(self):
        item = await _add_item(10, target_folder="")
        await _add_doc(item, ("x.pdf",))
        rows = _rows(await build_milestone_manifest(CUST, DEV, MS), 10)
        assert rows[0].carrier_destination == ""
        assert "target_folder" in rows[0].excluded_reason


class TestStateReporting:
    async def test_not_ready_item_still_lists_its_destinations(self):
        """State is a 'not yet', not a 'never'. Blanking the preview before a
        submission would defeat its purpose -- that is when TPMs look."""
        item = await _add_item(10, state="Open")
        await _add_doc(item, ("battery.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        (row,) = _rows(m, 10)
        assert row.carrier_destination == BATTERY + "/battery.pdf"
        assert row.excluded_reason == ""

    async def test_not_ready_item_is_flagged_separately(self):
        item = await _add_item(10, state="Open")
        await _add_doc(item, ("battery.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert [i.item_no for i in m.items_not_ready] == [10]

    async def test_ready_item_is_not_flagged(self):
        item = await _add_item(10, state="ReadyForSubmission")
        await _add_doc(item, ("battery.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert m.items_not_ready == []

    async def test_undeliverable_item_is_not_flagged_as_merely_not_ready(self):
        # no_customer_upload is a 'never'; it must not appear in the
        # "would go once state advances" warning.
        item = await _add_item(10, state="Open", no_upload=True)
        await _add_doc(item, ("x.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert m.items_not_ready == []


class TestManifestShape:
    async def test_items_are_ordered_and_scoped(self):
        for n in (14, 10):
            it = await _add_item(n)
            await _add_doc(it, (f"f{n}.pdf",))
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert [i.item_no for i in m.items] == [10, 14]
        assert m.customer_id == CUST and m.milestone_id == MS

    async def test_other_milestones_are_not_included(self):
        from core.src.storage.db import DeliveryItemTable, session_scope
        await _add_item(10)
        now = datetime.now(timezone.utc)
        async with session_scope() as s:
            s.add(DeliveryItemTable(
                item_id=f"{CUST}-{DEV}-DRR-5", customer_id=CUST, device_id=DEV,
                milestone_id="DRR", item_no=5,
                item_type="test_tech_waiver_report", item_name="drr",
                tg_name=TG, delivery_state="Open", last_updated=now,
                sort_order=5, path_id="p5",
            ))
            await s.commit()
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert [i.item_no for i in m.items] == [10]

    async def test_exactly_one_of_destination_or_reason_per_row(self):
        item = await _add_item(10)
        await _add_doc(item, ("ok.pdf",))
        await _add_doc(item, ("w.pdf",), doc_type="waiver")
        m = await build_milestone_manifest(CUST, DEV, MS)
        for r in _rows(m, 10):
            assert bool(r.carrier_destination) != bool(r.excluded_reason)

    async def test_empty_milestone_is_safe(self):
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert m.items == []
        assert m.total_included == 0 and m.total_excluded == 0
        assert m.items_not_ready == []

    async def test_counts_add_up(self):
        item = await _add_item(10)
        await _add_doc(item, ("a.pdf",))
        await _add_doc(item, ("b.pdf",))
        await _add_doc(item, ("w.pdf",), doc_type="waiver")
        m = await build_milestone_manifest(CUST, DEV, MS)
        assert m.total_included == 2
        assert m.total_excluded == 1
        it = m.items[0]
        assert it.included_count + it.excluded_count == len(it.rows)
