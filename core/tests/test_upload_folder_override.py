"""UPLOAD-FOLDER-OVERRIDE-1: TPM-chosen carrier folder.

Two properties carry the design, and both are load-bearing enough that the
feature is wrong without them:

  family scope   The override applies to the whole doc_id_slug revision
                 family. Keyed on a single file_hash, a TPM would redirect
                 rev1, the owner would send rev2, and rev2 would silently
                 upload to the ORIGINAL folder -- the mirror of the
                 family-split problem that ruled out re-assignment.

  offered set    Only folders already declared by a work item in the same TG
                 are selectable, so the TPM cannot invent a destination and
                 the override cannot paper over a missing template entry.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import configure_engine, init_db, list_upload_files_for_item
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.models import NSDPathType
from core.src.storage.upload_folder_override import (
    FolderOption,
    clear_upload_folder_override,
    list_folder_options_for_tg,
    set_upload_folder_override,
)

CUST, DEV, MS, TG = "MMK", "SM-S671U1", "P1", "HW PL"
ITEM = f"{CUST}-{DEV}-{MS}-10"
BATTERY = "Feature Test Results/Battery"
FCC = "RF Parametric Data/Documentation/FCC Package"


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


async def _item(item_no, *, folder=BATTERY, no_upload=False, tg=TG):
    from core.src.storage.db import DeliveryItemTable, session_scope
    now = datetime.now(timezone.utc)
    item_id = f"{CUST}-{DEV}-{MS}-{item_no}"
    async with session_scope() as s:
        s.add(DeliveryItemTable(
            item_id=item_id, customer_id=CUST, device_id=DEV, milestone_id=MS,
            item_no=item_no, item_type="test_tech_waiver_report",
            item_name=f"Item {item_no}", tg_name=tg,
            delivery_state="ReadyForSubmission", target_folder=folder,
            no_customer_upload=no_upload, last_updated=now,
            sort_order=item_no, path_id=f"p{item_no}",
        ))
        await s.commit()
    return item_id


async def _doc(item_id, file_hash, *, slug="fam", rev=1, filename=None):
    """One revision. Same slug => same family."""
    from core.src.storage.db import (
        DocumentIndexTable, DocumentItemAssociationTable, session_scope,
    )
    filename = filename or f"{file_hash[:4]}.pdf"
    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        s.add(DocumentIndexTable(
            file_hash=file_hash, milestone_id=MS, doc_type="test_report",
            doc_id_slug=slug, rev_number=rev,
            ingest_source="NetworkSharedDrive", original_filename=filename,
            inferred_tg_name=TG, routing_resolution="SubstringMatch",
            ingested_at=now,
        ))
        s.add(DocumentItemAssociationTable(
            file_hash=file_hash, delivery_item_id=item_id, milestone_id=MS,
            local_nsd_path=f"internal/{CUST}/{DEV}/{MS}/{TG}/item/rev{rev}/{filename}",
            nsd_path_type=NSDPathType.CLASSIFIED.value,
            owner_corp_id="", associated_at=now,
        ))
        await s.commit()


async def _override_of(file_hash, item_id=ITEM):
    from core.src.storage.db import DocumentItemAssociationTable, session_scope
    async with session_scope() as s:
        row = await s.get(DocumentItemAssociationTable, (file_hash, item_id))
        return row.upload_target_folder_override if row else None


class TestFolderOptions:
    async def test_lists_distinct_folders_in_the_tg(self):
        await _item(10, folder=BATTERY)
        await _item(14, folder=FCC)
        opts = await list_folder_options_for_tg(CUST, DEV, MS, TG)
        assert [o.target_folder for o in opts] == sorted([BATTERY, FCC])

    async def test_a_folder_shared_by_several_items_lists_all_item_nos(self):
        # The case that argues for overriding the folder instead of
        # re-assigning the item: the choice says nothing about which item.
        await _item(14, folder=FCC)
        await _item(20, folder=FCC)
        (opt,) = await list_folder_options_for_tg(CUST, DEV, MS, TG)
        assert opt.item_nos == (14, 20)
        assert opt.label == FCC + "  (#14, #20)"

    async def test_scoped_to_the_tg_not_the_milestone(self):
        await _item(10, folder=BATTERY, tg=TG)
        await _item(47, folder="Other/Folder", tg="MNO-Solution")
        opts = await list_folder_options_for_tg(CUST, DEV, MS, TG)
        assert [o.target_folder for o in opts] == [BATTERY]

    async def test_no_customer_upload_items_are_not_offered(self):
        await _item(10, folder=BATTERY)
        await _item(11, folder="Never/Delivered", no_upload=True)
        opts = await list_folder_options_for_tg(CUST, DEV, MS, TG)
        assert [o.target_folder for o in opts] == [BATTERY]

    async def test_items_without_a_folder_are_not_offered(self):
        await _item(10, folder=BATTERY)
        await _item(11, folder="")
        opts = await list_folder_options_for_tg(CUST, DEV, MS, TG)
        assert [o.target_folder for o in opts] == [BATTERY]

    async def test_empty_tg_yields_no_options(self):
        assert await list_folder_options_for_tg(CUST, DEV, MS, "NOPE") == []

    def test_label_without_items(self):
        assert FolderOption("A/B", ()).label == "A/B"


class TestFamilyScope:
    """The property that makes the override survive an owner resend."""

    async def test_override_applies_to_every_revision_in_the_family(self):
        await _item(10)
        await _doc(ITEM, "a" * 64, slug="fam", rev=1)
        await _doc(ITEM, "b" * 64, slug="fam", rev=2)
        n = await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        assert n == 2
        assert await _override_of("a" * 64) == FCC
        assert await _override_of("b" * 64) == FCC

    async def test_a_later_revision_inherits_it(self):
        # The failure this prevents: TPM redirects rev1, owner sends rev2,
        # rev2 uploads to the ORIGINAL folder.
        await _item(10)
        await _doc(ITEM, "a" * 64, slug="fam", rev=1)
        await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        await _doc(ITEM, "b" * 64, slug="fam", rev=2)
        # rev2 arrives after the override; re-applying picks it up.
        n = await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        assert n == 2
        assert await _override_of("b" * 64) == FCC

    async def test_a_different_family_is_untouched(self):
        await _item(10)
        await _doc(ITEM, "a" * 64, slug="fam_one", rev=1)
        await _doc(ITEM, "c" * 64, slug="fam_two", rev=1)
        await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        assert await _override_of("a" * 64) == FCC
        assert await _override_of("c" * 64) is None

    async def test_document_with_no_slug_is_its_own_family(self):
        from core.src.storage.db import (
            DocumentItemAssociationTable, session_scope,
        )
        await _item(10)
        now = datetime.now(timezone.utc)
        async with session_scope() as s:
            s.add(DocumentItemAssociationTable(
                file_hash="d" * 64, delivery_item_id=ITEM, milestone_id=MS,
                local_nsd_path="internal/x/lonely.pdf",
                nsd_path_type=NSDPathType.CLASSIFIED.value,
                owner_corp_id="", associated_at=now,
            ))
            await s.commit()
        n = await set_upload_folder_override(
            file_hash="d" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        assert n == 1
        assert await _override_of("d" * 64) == FCC


class TestSetAndClear:
    async def test_clear_reverts_the_whole_family(self):
        await _item(10)
        await _doc(ITEM, "a" * 64, slug="fam", rev=1)
        await _doc(ITEM, "b" * 64, slug="fam", rev=2)
        await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        n = await clear_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM, tpm_id="tpm@corp",
        )
        assert n == 2
        assert await _override_of("a" * 64) is None
        assert await _override_of("b" * 64) is None

    async def test_empty_folder_is_rejected(self):
        await _item(10)
        await _doc(ITEM, "a" * 64)
        with pytest.raises(ValueError):
            await set_upload_folder_override(
                file_hash="a" * 64, delivery_item_id=ITEM,
                target_folder="   ", tpm_id="tpm@corp",
            )

    async def test_unknown_document_updates_nothing(self):
        await _item(10)
        n = await set_upload_folder_override(
            file_hash="z" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        assert n == 0

    async def test_the_change_is_audited(self):
        from core.src.storage.audit_ops import query_communications
        await _item(10)
        await _doc(ITEM, "a" * 64)
        await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        rows = await query_communications(action_type="set_upload_folder_override")
        assert rows and FCC in (rows[0].summary or "")


class TestUploadResolverSeesTheOverride:
    async def test_override_reaches_list_upload_files_for_item(self):
        await _item(10)
        await _doc(ITEM, "a" * 64)
        before = await list_upload_files_for_item(ITEM)
        assert before[0].target_folder_override == ""
        await set_upload_folder_override(
            file_hash="a" * 64, delivery_item_id=ITEM,
            target_folder=FCC, tpm_id="tpm@corp",
        )
        after = await list_upload_files_for_item(ITEM)
        assert after[0].target_folder_override == FCC

    async def test_no_override_leaves_the_field_empty(self):
        await _item(10)
        await _doc(ITEM, "a" * 64)
        files = await list_upload_files_for_item(ITEM)
        assert files and files[0].target_folder_override == ""
