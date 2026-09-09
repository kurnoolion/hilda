"""DRRP1-1 chunk 2 (2026-09-01) -- resolving a source milestone's documents.

Deliverables collected against a DRR work-item are submitted as part of P1.
Mapped DRR items are `no_customer_upload: true`, so this resolver is the only
route those documents take to the carrier -- a silent miss here means a
collected document that is never delivered.

The resolver delegates to `list_upload_files_for_item`, so revision-family
collapse, current-version resolution, waiver exclusion and the flat/archive
decision are all inherited rather than reimplemented. Tests here cover the
MAPPING and TAGGING behaviour plus the miss paths; the inherited behaviour is
covered in test_upload_view_resolution.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import (
    add_document_index_row,
    add_document_item_association,
    configure_engine,
    init_db,
    list_migrated_upload_files_for_item,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.models import (
    DocumentIndexRow, DocumentItemAssociation, NSDPathType, RoutingResolution,
)
from core.src.template_schema import milestone_item_mapping as mim
from core.src.template_schema import DocType, IngestSource

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
CUST, DEVICE = "MMK", "SM-S671U1"
DRR, P1 = "DRR", "P1"
TG = "HW PL"

# Real MMK pair: DRR #50 (LTE OTA) -> P1 #10.
DRR_NO, P1_NO = 50, 10
DRR_ITEM = f"{CUST}-{DEVICE}-{DRR}-{DRR_NO}"
P1_ITEM = f"{CUST}-{DEVICE}-{P1}-{P1_NO}"


@pytest.fixture(autouse=True)
async def env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    mim.clear_cache()
    # Load the SHIPPED MMK mapping -- these tests exercise the real pairs.
    assert mim.load_customer_mapping(CUST) is True
    yield
    mim.clear_cache()
    await engine.dispose()
    set_storage_config(None)


async def _mk_item(milestone: str, item_no: int, *, tg: str = TG) -> str:
    """Insert a delivery_item row. Only the scope columns the resolver reads
    matter, but the table requires the non-null ones."""
    from core.src.storage.db import DeliveryItemTable, session_scope
    item_id = f"{CUST}-{DEVICE}-{milestone}-{item_no}"
    async with session_scope() as s:
        s.add(DeliveryItemTable(
            item_id=item_id, item_no=item_no, customer_id=CUST,
            device_id=DEVICE, milestone_id=milestone, item_name=f"item {item_no}",
            item_type=DocType.TEST_REPORT.value and "test_tech_waiver_report",
            delivery_state="Open", last_updated=NOW, sort_order=item_no,
            path_id=f"p{item_no}", tg_name=tg,
        ))
        await s.commit()
    return item_id


async def _give_document(item_id: str, milestone: str, filename: str, *,
                         body: bytes, slug: str,
                         doc_type: DocType = DocType.TEST_REPORT,
                         tg: str = TG) -> str:
    """Router-shaped ingest against one item: view-tree save + index +
    association."""
    row = await save_view_document(
        customer_id=CUST, device_id=DEVICE, milestone_id=milestone,
        tg_name=tg, relative_parts=(filename,), content=body,
        saved_by="auto", source="router",
    )
    await add_document_index_row(DocumentIndexRow(
        file_hash=row.sha256, milestone_id=milestone, doc_type=doc_type,
        doc_id_slug=slug, rev_number=1, ingest_source=IngestSource.EMAIL,
        original_filename=filename,
        routing_resolution=RoutingResolution.SUBSTRING_MATCH, ingested_at=NOW,
    ))
    await add_document_item_association(DocumentItemAssociation(
        file_hash=row.sha256, delivery_item_id=item_id, milestone_id=milestone,
        local_nsd_path=f"internal/{slug}/rev1/{filename}",
        nsd_path_type=NSDPathType.CLASSIFIED, owner_corp_id="owner-001",
        associated_at=NOW,
    ))
    return row.sha256


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestMigratedResolution:
    async def test_drr_document_resolves_for_the_mapped_p1_item(self):
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"drr-doc", slug="lte_ota_test_report",
        )

        files = await list_migrated_upload_files_for_item(P1_ITEM)
        assert len(files) == 1
        assert files[0].filename == "lte_ota_test_report.xlsx"

    async def test_files_are_tagged_with_their_source(self):
        """The audit trail and UI must be able to say 'from DRR #50' rather
        than presenting a borrowed document as native to P1."""
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"drr-doc", slug="lte_ota_test_report",
        )

        f = (await list_migrated_upload_files_for_item(P1_ITEM))[0]
        assert f.migrated_from_milestone == DRR
        assert f.migrated_from_item_no == DRR_NO

    async def test_resolves_to_the_drr_view_path_not_p1(self):
        """No rows are created: the document stays in the DRR trees."""
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"drr-doc", slug="lte_ota_test_report",
        )

        f = (await list_migrated_upload_files_for_item(P1_ITEM))[0]
        assert f.is_view is True
        assert f.relative_path.startswith(f"view/{CUST}/{DEVICE}/{DRR}/")

    async def test_creates_no_new_rows(self):
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"drr-doc", slug="lte_ota_test_report",
        )
        before = await _row_counts()
        await list_migrated_upload_files_for_item(P1_ITEM)
        assert await _row_counts() == before

    async def test_waivers_are_not_migrated(self):
        """Waivers are never uploaded, any milestone -- the inherited filter in
        list_upload_files_for_item must still apply through the mapping."""
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "legal_waiver.pdf", body=b"w",
            slug="legal_waiver", doc_type=DocType.WAIVER,
        )
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []

    async def test_only_the_winning_revision_migrates(self):
        """Family collapse is inherited: two revisions of one DRR document
        contribute a single file."""
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"rev1", slug="lte_ota_test_report",
        )
        # Second revision of the same family, same filename -> same view path.
        from core.src.storage.db import session_scope
        row = await save_view_document(
            customer_id=CUST, device_id=DEVICE, milestone_id=DRR, tg_name=TG,
            relative_parts=("lte_ota_test_report.xlsx",), content=b"rev2",
            saved_by="auto", source="router",
        )
        await add_document_index_row(DocumentIndexRow(
            file_hash=row.sha256, milestone_id=DRR, doc_type=DocType.TEST_REPORT,
            doc_id_slug="lte_ota_test_report", rev_number=2,
            ingest_source=IngestSource.EMAIL,
            original_filename="lte_ota_test_report.xlsx",
            routing_resolution=RoutingResolution.SUBSTRING_MATCH, ingested_at=NOW,
        ))
        await add_document_item_association(DocumentItemAssociation(
            file_hash=row.sha256, delivery_item_id=DRR_ITEM, milestone_id=DRR,
            local_nsd_path="internal/lte_ota_test_report/rev2/lte_ota_test_report.xlsx",
            nsd_path_type=NSDPathType.CLASSIFIED, owner_corp_id="owner-001",
            associated_at=NOW,
        ))

        files = await list_migrated_upload_files_for_item(P1_ITEM)
        assert len(files) == 1


# ---------------------------------------------------------------------------
# Miss paths -- all silent, per "no guarantee of file existence"
# ---------------------------------------------------------------------------


class TestMissPaths:
    async def test_unmapped_p1_item_yields_nothing(self):
        """P1 #1 is not a mapping target."""
        unmapped = await _mk_item(P1, 1)
        assert await list_migrated_upload_files_for_item(unmapped) == []

    async def test_intentionally_unmapped_drr_items_contribute_nothing(self):
        """DRR #70 / #76 are closed by TPM directly and map nowhere, so no P1
        item should pick them up."""
        for no in (70, 76):
            assert mim.get_target_item_no(
                customer_id=CUST, source_milestone=DRR, source_item_no=no,
            ) is None

    async def test_missing_drr_item_yields_nothing(self):
        """Mapping names a DRR item this device never had."""
        await _mk_item(P1, P1_NO)
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []

    async def test_drr_item_with_no_documents_yields_nothing(self):
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []

    async def test_unknown_target_item_yields_nothing(self):
        assert await list_migrated_upload_files_for_item("NO-SUCH-ITEM") == []

    async def test_no_mapping_loaded_yields_nothing(self):
        mim.clear_cache()
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            DRR_ITEM, DRR, "lte_ota_test_report.xlsx",
            body=b"drr-doc", slug="lte_ota_test_report",
        )
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []

    async def test_p1_own_documents_are_not_returned(self):
        """This resolver returns ONLY migrated files; the item's own documents
        come from list_upload_files_for_item. Chunk 3 combines them."""
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        await _give_document(
            P1_ITEM, P1, "p1_native_test_report.xlsx",
            body=b"p1-doc", slug="p1_native_test_report",
        )
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []


# ---------------------------------------------------------------------------
# Cross-device isolation
# ---------------------------------------------------------------------------


class TestDeviceScoping:
    async def test_another_devices_drr_document_is_not_migrated(self):
        """The mapping is per-carrier but resolution must stay per-device --
        SM-S671U1's P1 item must not pick up SM-F918U's DRR documents."""
        other_device = "SM-F918U"
        from core.src.storage.db import DeliveryItemTable, session_scope
        other_drr = f"{CUST}-{other_device}-{DRR}-{DRR_NO}"
        async with session_scope() as s:
            s.add(DeliveryItemTable(
                item_id=other_drr, item_no=DRR_NO, customer_id=CUST,
                device_id=other_device, milestone_id=DRR, item_name="x",
                item_type="test_tech_waiver_report", delivery_state="Open",
                last_updated=NOW, sort_order=1, path_id="p", tg_name=TG,
            ))
            await s.commit()
        row = await save_view_document(
            customer_id=CUST, device_id=other_device, milestone_id=DRR,
            tg_name=TG, relative_parts=("other_test_report.xlsx",),
            content=b"other", saved_by="auto", source="router",
        )
        await add_document_index_row(DocumentIndexRow(
            file_hash=row.sha256, milestone_id=DRR, doc_type=DocType.TEST_REPORT,
            doc_id_slug="other_test_report", rev_number=1,
            ingest_source=IngestSource.EMAIL,
            original_filename="other_test_report.xlsx",
            routing_resolution=RoutingResolution.SUBSTRING_MATCH, ingested_at=NOW,
        ))
        await add_document_item_association(DocumentItemAssociation(
            file_hash=row.sha256, delivery_item_id=other_drr, milestone_id=DRR,
            local_nsd_path="internal/other_test_report/rev1/other_test_report.xlsx",
            nsd_path_type=NSDPathType.CLASSIFIED, owner_corp_id="o",
            associated_at=NOW,
        ))

        await _mk_item(P1, P1_NO)          # SM-S671U1's P1 item, no DRR item
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []


class TestResolveMigrationSource:
    """DRRP1-1 chunk 4 (2026-09-01): the shared resolution both consumers use.

    Extracted so `submit_to_carrier` (which needs the source's uploadable
    FILES) and the dashboard document section (which needs its INDEX rows +
    associations, to reuse the own-documents rendering path) cannot drift.
    """

    async def test_resolves_the_mapped_source(self):
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)

        from core.src.storage import resolve_migration_source
        src = await resolve_migration_source(P1_ITEM)
        assert src is not None
        assert src.item_id == DRR_ITEM
        assert (src.milestone, src.item_no) == (DRR, DRR_NO)
        assert (src.target_milestone, src.target_item_no) == (P1, P1_NO)
        assert (src.customer_id, src.device_id) == (CUST, DEVICE)

    async def test_none_for_unmapped_target(self):
        from core.src.storage import resolve_migration_source
        unmapped = await _mk_item(P1, 1)
        assert await resolve_migration_source(unmapped) is None

    async def test_none_when_source_item_absent(self):
        from core.src.storage import resolve_migration_source
        await _mk_item(P1, P1_NO)
        assert await resolve_migration_source(P1_ITEM) is None

    async def test_none_for_unknown_target(self):
        from core.src.storage import resolve_migration_source
        assert await resolve_migration_source("NO-SUCH-ITEM") is None

    async def test_does_not_require_the_source_to_have_documents(self):
        """The dashboard needs the source item even when it has nothing yet —
        unlike the upload path, which short-circuits on an empty file set."""
        from core.src.storage import resolve_migration_source
        await _mk_item(DRR, DRR_NO)
        await _mk_item(P1, P1_NO)
        src = await resolve_migration_source(P1_ITEM)
        assert src is not None
        assert await list_migrated_upload_files_for_item(P1_ITEM) == []


async def _row_counts() -> tuple[int, int, int]:
    from sqlalchemy import func, select as _select
    from core.src.storage.db import (
        DocumentIndexTable, DocumentItemAssociationTable, DocumentVersionTable,
        session_scope,
    )
    async with session_scope() as s:
        out = []
        for t in (DocumentIndexTable, DocumentItemAssociationTable,
                  DocumentVersionTable):
            out.append(int((await s.execute(
                _select(func.count()).select_from(t)
            )).scalar() or 0))
    return tuple(out)
