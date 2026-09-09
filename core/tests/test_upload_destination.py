"""UPLOAD-DEST-1: carrier destination surfaced per document.

The TPM's question is "where does this land on Google Drive", not "which work
item holds it". Nothing in the UI answered that, and the answer was only
computable inside submit_to_carrier's item loop -- so a document could be
silently excluded (DOCTYPE-MISALIGN-UI-1) or land somewhere unexpected with no
way to check first.

Both halves are covered here: the pure resolution function, and the value
actually reaching TgFileEntry through list_files_in_tg. The destination is
computed via storage.upload_plan -- the same functions submit_to_carrier
calls -- so what the UI shows is what gets delivered.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import (
    configure_engine,
    init_db,
    list_files_in_tg,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.document_view_ops import resolve_carrier_destination
from core.src.storage.models import NSDPathType


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


LTE_OTA = "RF Parametric Data/Antenna Test Results/LTE OTA"


class TestResolveCarrierDestination:
    """Pure half. Exactly one of (destination, reason) is non-empty, and the
    exclusion set mirrors what submit_to_carrier actually skips."""

    BASE = dict(
        view_relative_path="view/MMK/SM-S671U1/P1/HW PL/report.pdf",
        filename="report.pdf",
        doc_type="test_report",
        from_zip=False,
        target_folder=LTE_OTA,
        no_customer_upload=False,
        is_superseded=False,
        is_staged_not_classified=False,
    )

    def _r(self, **over):
        return resolve_carrier_destination(**{**self.BASE, **over})

    # ---- DRRP1-DEST-1 (2026-09-08) ------------------------------------
    # Every mapped DRR item is no_customer_upload with target_folder NULL, so
    # a DRR page reported "not uploaded" for documents that DO reach the
    # carrier under P1 via list_migrated_upload_files_for_item -- the opposite
    # of what happens, on the one milestone whose purpose is feeding P1, and
    # the only page those files are listed on.

    def test_no_customer_upload_with_a_mapped_target_reports_the_path(self):
        dest, reason = self._r(
            view_relative_path="view/MMK/SM-S671U1/DRR/HW PL/report.pdf",
            no_customer_upload=True,
            target_folder="",
            migrated_target_folder=LTE_OTA,
        )
        assert reason == ""
        assert dest == f"{LTE_OTA}/report.pdf"

    def test_no_customer_upload_without_a_mapping_still_reports_it(self):
        """Unmapped DRR items genuinely never deliver -- #70 and #76 are
        closed by the TPM directly. The reason must survive for those."""
        dest, reason = self._r(no_customer_upload=True, target_folder="")
        assert dest == ""
        assert "no_customer_upload" in reason

    def test_override_beats_the_mapped_target_folder(self):
        """Same precedence submit_to_carrier applies. The override is stored
        on the SOURCE association and travels through the migration, so a TPM
        redirect set on the DRR page has to win here too."""
        dest, _ = self._r(
            no_customer_upload=True,
            target_folder="",
            migrated_target_folder=LTE_OTA,
            target_folder_override="Documentation/Elsewhere",
        )
        assert dest == "Documentation/Elsewhere/report.pdf"

    def test_waiver_still_wins_over_a_mapped_target(self):
        """Waivers are dropped for every milestone; a mapping must not
        smuggle one to the carrier."""
        dest, reason = self._r(
            doc_type="waiver", no_customer_upload=True, target_folder="",
            migrated_target_folder=LTE_OTA,
        )
        assert dest == "" and "waiver" in reason

    def test_staged_still_wins_over_a_mapped_target(self):
        dest, reason = self._r(
            no_customer_upload=True, target_folder="",
            migrated_target_folder=LTE_OTA,
            is_staged_not_classified=True,
        )
        assert dest == "" and "staged" in reason

    def test_plain_file_lands_flat_under_target_folder(self):
        dest, reason = self._r()
        assert reason == ""
        assert dest == LTE_OTA + "/report.pdf"

    def test_zip_derived_file_keeps_its_subfolder(self):
        dest, reason = self._r(
            view_relative_path="view/MMK/SM-S671U1/P1/HW PL/UCTP/report.pdf",
            from_zip=True,
        )
        assert reason == ""
        assert dest == LTE_OTA + "/UCTP/report.pdf"

    def test_nsd_folder_segments_do_not_become_carrier_folders(self):
        # UPLOAD-FLAT-1 regression: identical path shape, from_zip=False.
        dest, _ = self._r(
            view_relative_path="view/MMK/SM-S671U1/P1/HW PL/2. DMDform (Done)/r.pdf",
            filename="r.pdf",
            from_zip=False,
        )
        assert dest == LTE_OTA + "/r.pdf"
        assert "DMDform" not in dest

    def test_nested_zip_folders_are_preserved_containers_stripped(self):
        dest, _ = self._r(
            view_relative_path="view/MMK/SM-S671U1/P1/HW PL/o.zip/5G/LC/x.pdf",
            filename="x.pdf",
            from_zip=True,
        )
        assert dest == LTE_OTA + "/5G/LC/x.pdf"

    @pytest.mark.parametrize("over,fragment", [
        ({"doc_type": "waiver"},             "waiver"),
        ({"doc_type": "archive_container"},  "archive container"),
        ({"is_superseded": True},            "superseded"),
        ({"is_staged_not_classified": True}, "staged"),
        ({"no_customer_upload": True},       "no_customer_upload"),
        ({"target_folder": ""},              "no target_folder"),
    ])
    def test_exclusions_report_a_reason_and_no_path(self, over, fragment):
        dest, reason = self._r(**over)
        assert dest == ""
        assert fragment in reason

    def test_waiver_reason_wins_over_superseded(self):
        # Ordering is deliberate: report the most specific reason.
        _, reason = self._r(doc_type="waiver", is_superseded=True)
        assert "waiver" in reason

    def test_delivery_state_is_deliberately_not_considered(self):
        # submit_to_carrier requires ReadyForSubmission, but that is a "not
        # yet" rather than a "never". Applying it here would blank the view
        # before every submission -- exactly when a TPM wants to look.
        dest, reason = self._r()
        assert dest and not reason


class TestListFilesInTgCarrierDestination:
    """Wired half -- the value reaches TgFileEntry via list_files_in_tg."""

    SCOPE = dict(
        customer_id="MMK", device_id="SM-S671U1",
        milestone_id="P1", tg_name="HW PL",
    )

    async def _seed(self, rel_parts, *, doc_type="test_report",
                    target_folder="Feature Test Results/Battery",
                    no_customer_upload=False, from_zip=False,
                    nsd_path_type=None):
        from core.src.storage.db import (
            DeliveryItemTable, DocumentIndexTable,
            DocumentItemAssociationTable, session_scope,
        )
        nsd_path_type = nsd_path_type or NSDPathType.CLASSIFIED.value
        await save_view_document(
            **self.SCOPE, relative_parts=rel_parts,
            # Content must differ per file: document_index.file_hash is the
            # sole PK, so identical bytes cannot be indexed twice.
            content=("bytes:" + "/".join(rel_parts)).encode(), saved_by="auto",
        )
        rel_path = "view/MMK/SM-S671U1/P1/HW PL/" + "/".join(rel_parts)
        fh = next(f.file_hash for f in await list_files_in_tg(**self.SCOPE)
                  if f.view_relative_path == rel_path)
        item_id = "MMK-SM-S671U1-P1-88"
        now = datetime.now(timezone.utc)
        async with session_scope() as session:
            # _seed may be called more than once per test; the work item is
            # shared, the documents are not.
            if await session.get(DeliveryItemTable, item_id) is None:
                session.add(DeliveryItemTable(
                    item_id=item_id, customer_id="MMK", device_id="SM-S671U1",
                    milestone_id="P1", item_no=88,
                    item_type="test_tech_waiver_report", item_name="Item 88",
                    tg_name="HW PL", delivery_state="Open",
                    target_folder=target_folder,
                    no_customer_upload=no_customer_upload,
                    last_updated=now, sort_order=88, path_id="p88",
                    ))
            session.add(DocumentIndexTable(
                file_hash=fh, milestone_id="P1", doc_type=doc_type,
                doc_id_slug="slug_" + rel_parts[-1], rev_number=1,
                ingest_source="NetworkSharedDrive",
                original_filename=rel_parts[-1], inferred_tg_name="HW PL",
                routing_resolution="SubstringMatch", from_zip=from_zip,
                ingested_at=now,
            ))
            session.add(DocumentItemAssociationTable(
                file_hash=fh, delivery_item_id=item_id, milestone_id="P1",
                local_nsd_path="internal/x/" + "/".join(rel_parts),
                nsd_path_type=nsd_path_type, owner_corp_id="",
                associated_at=now,
            ))
            await session.commit()
        return next(f for f in await list_files_in_tg(**self.SCOPE)
                    if f.view_relative_path == rel_path)

    async def test_destination_reaches_the_entry(self):
        f = await self._seed(("battery.pdf",))
        assert f.upload_excluded_reason == ""
        assert f.carrier_destination == "Feature Test Results/Battery/battery.pdf"

    async def test_zip_subfolder_appears_in_destination(self):
        f = await self._seed(("DOU", "battery.pdf"), from_zip=True)
        assert f.carrier_destination == \
            "Feature Test Results/Battery/DOU/battery.pdf"

    async def test_nsd_subfolder_does_not(self):
        f = await self._seed(("DOU", "battery.pdf"), from_zip=False)
        assert f.carrier_destination == \
            "Feature Test Results/Battery/battery.pdf"

    async def test_waiver_excluded_with_reason(self):
        f = await self._seed(("bt_waiver.pdf",), doc_type="waiver")
        assert f.carrier_destination == ""
        assert "waiver" in f.upload_excluded_reason

    async def test_misaligned_doc_reports_staged(self):
        f = await self._seed(
            ("odd.pdf",),
            nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
        )
        assert f.carrier_destination == ""
        assert "staged" in f.upload_excluded_reason
        assert f.is_staged_not_classified is True

    async def test_item_with_no_target_folder_reports_it(self):
        f = await self._seed(("x.pdf",), target_folder="")
        assert f.carrier_destination == ""
        assert "target_folder" in f.upload_excluded_reason

    async def test_no_customer_upload_item_reports_it(self):
        f = await self._seed(("x.pdf",), no_customer_upload=True)
        assert f.carrier_destination == ""
        assert "no_customer_upload" in f.upload_excluded_reason

    async def test_orphan_with_no_association_is_excluded(self):
        await save_view_document(
            **self.SCOPE, relative_parts=("orphan.pdf",),
            content=b"orphan", saved_by="auto",
        )
        f = (await list_files_in_tg(**self.SCOPE))[0]
        assert f.carrier_destination == ""
        assert f.upload_excluded_reason != ""

    async def test_undeliverable_documents_sort_first(self):
        """SORT-DEST-1: rows that will NOT be delivered lead the page.

        Seeded out of the target order deliberately -- the previous sort was
        by view_relative_path, under which 'a.pdf' led and the two rows
        needing a decision sat below the ones already fine.
        """
        await self._seed(("a.pdf",))                       # deliverable
        await self._seed(("w.pdf",), doc_type="waiver")     # excluded: waiver
        await self._seed(                                   # excluded: staged
            ("s.pdf",),
            nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
        )
        files = await list_files_in_tg(**self.SCOPE)
        names = [f.filename for f in files]

        # Both exclusions precede the deliverable row.
        assert names.index("a.pdf") == len(names) - 1, names
        # Reasons group together and sort among themselves: "staged ..."
        # before "waiver ...".
        assert names[:2] == ["s.pdf", "w.pdf"], names
        assert all(f.upload_excluded_reason for f in files[:2])
        assert files[-1].carrier_destination and not files[-1].upload_excluded_reason

    async def test_deliverable_rows_sort_by_destination(self):
        """Within the delivered group the order follows the carrier path, so
        the page reads in the shape the carrier folder will have."""
        await self._seed(("m.pdf",))
        await self._seed(("zip.zip", "AAA", "inner.pdf"), from_zip=True)
        files = await list_files_in_tg(**self.SCOPE)
        deliverable = [f for f in files if not f.upload_excluded_reason]
        dests = [f.carrier_destination for f in deliverable]
        assert dests == sorted(dests, key=str.lower), dests

    async def test_exactly_one_of_the_two_is_always_set(self):
        """The contract the template relies on -- it renders the path when
        present, else the reason, and must never show both or neither."""
        await self._seed(("battery.pdf",))
        await self._seed(("second_waiver.pdf",), doc_type="waiver")
        for f in await list_files_in_tg(**self.SCOPE):
            assert bool(f.carrier_destination) != bool(f.upload_excluded_reason), \
                f.view_relative_path


class TestDestinationReflectsTheOverride:
    """UPLOAD-FOLDER-OVERRIDE-1: the TG view must show the REDIRECTED path.

    If the column kept showing the work item's folder while the uploader used
    the override, the display would be lying -- the exact drift that
    UPLOAD-PLAN-1 exists to prevent.
    """

    BASE = dict(
        view_relative_path="view/MMK/SM-S671U1/P1/HW PL/report.pdf",
        filename="report.pdf",
        doc_type="test_report",
        from_zip=False,
        target_folder=LTE_OTA,
        no_customer_upload=False,
        is_superseded=False,
        is_staged_not_classified=False,
    )
    FCC = "RF Parametric Data/Documentation/FCC Package"

    def _r(self, **over):
        return resolve_carrier_destination(**{**self.BASE, **over})

    def test_override_replaces_the_item_folder(self):
        dest, reason = self._r(target_folder_override=self.FCC)
        assert reason == ""
        assert dest == self.FCC + "/report.pdf"

    def test_subdir_still_rides_under_the_override(self):
        dest, _ = self._r(
            view_relative_path="view/MMK/SM-S671U1/P1/HW PL/UCTP/report.pdf",
            from_zip=True,
            target_folder_override=self.FCC,
        )
        assert dest == self.FCC + "/UCTP/report.pdf"

    def test_empty_override_falls_back_to_the_item_folder(self):
        for value in ("", "   "):
            dest, _ = self._r(target_folder_override=value)
            assert dest == LTE_OTA + "/report.pdf"

    def test_override_rescues_an_item_with_no_target_folder(self):
        # Without the override this is excluded; with one it is deliverable.
        excluded, reason = self._r(target_folder="")
        assert excluded == "" and "target_folder" in reason
        dest, reason2 = self._r(target_folder="", target_folder_override=self.FCC)
        assert reason2 == "" and dest == self.FCC + "/report.pdf"

    def test_override_does_not_rescue_a_waiver(self):
        # Exclusions that are about the DOCUMENT still win over a folder pick.
        dest, reason = self._r(doc_type="waiver", target_folder_override=self.FCC)
        assert dest == "" and "waiver" in reason
