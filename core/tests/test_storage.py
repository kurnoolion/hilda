"""storage tests — NSD paths/IO, document index, associations, fan-out, tokens,
redis TTL cap, overrides, folder routing, tag catalog, comm-log, CLI smoke."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest

from core.src.diagnostics import QC_REGISTRY
from core.src.diagnostics.error_codes import ERROR_CODES, PipelineError
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage import (
    AutomationRuleOverride,
    BatchIdempotencyKey,
    Channel,
    CommunicationLogRow,
    Direction,
    DocumentIndexRow,
    DocumentItemAssociation,
    NSDPath,
    NSDPathType,
    RevisionResolution,
    RoutingResolution,
    TGFolderRoutingRow,
    TagCatalogRow,
    add_document_index_row,
    add_document_item_association,
    cache_delete,
    cache_get,
    cache_set,
    check_batch_idempotency,
    clear_override,
    compute_file_hash,
    configure_engine,
    deactivate_tag,
    delete_document_item_association,
    extract_first_page,
    fan_out_plm_associations,
    find_doc_id_slugs_for_item,
    get_active_overrides,
    get_document_index_row_by_hash,
    get_document_index_row_by_slug,
    get_documents_for_item,
    get_folder_routing_for_tg,
    get_tag_catalog,
    init_db,
    list_active_overrides,
    list_all_override_rule_ids,
    list_associations_for_file,
    list_inbound_drops,
    list_revisions,
    log_communication,
    make_download_token,
    query_communications,
    reactivate_tag,
    read_file,
    reassign_document_to_workitem,
    record_batch_idempotency,
    resolve_download_token,
    set_folder_routing_for_tg,
    set_is_final,
    set_override,
    set_redis_client,
    tpm_resolve_doc_type,
    tpm_resolve_revision,
    update_association_plm_attachment,
    update_review_findings,
    upsert_tag,
    write_file,
)
from core.src.template_schema import DocType, IngestSource, RuleScope

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    """Fresh in-memory DB + fakeredis + tmp NSD mount per test. Engine and redis
    client are disposed on teardown — each test runs in its own event loop, and
    connections bound to a dead loop poison the next test."""
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


def make_doc(file_hash: str = HASH_A, slug: str | None = "report-a", rev: int | None = 1,
             milestone: str = "ms-1", **kw) -> DocumentIndexRow:
    defaults = dict(
        file_hash=file_hash, milestone_id=milestone, doc_type=DocType.TEST_REPORT,
        doc_id_slug=slug, rev_number=rev, ingest_source=IngestSource.EMAIL,
        original_filename="Report A.xlsx",
        routing_resolution=RoutingResolution.SUBSTRING_MATCH, ingested_at=NOW,
    )
    defaults.update(kw)
    return DocumentIndexRow(**defaults)


def make_assoc(file_hash: str = HASH_A, item: str = "item-1", milestone: str = "ms-1",
               owner_corp_id: str = "y.vasilyev", owner_email: str = "owner@corp",
               plm: str | None = "PLM-1", **kw) -> DocumentItemAssociation:
    """Test fixture. owner_corp_id is the PLM grouping key per FR-5 + [D-035];
    owner_email kept as legacy parameter name for backward-compat with existing tests —
    routes to owner_corp_usa_email field."""
    path = NSDPath.internal_classified("carrier-a", "device-x", milestone, "tg-hw",
                                       item, "test_report", "report-a", 1)
    defaults = dict(
        file_hash=file_hash, delivery_item_id=item, milestone_id=milestone,
        local_nsd_path=path.to_relative(), nsd_path_type=NSDPathType.CLASSIFIED,
        owner_corp_id=owner_corp_id, owner_corp_usa_email=owner_email,
        plm_id=plm, associated_at=NOW,
    )
    defaults.update(kw)
    return DocumentItemAssociation(**defaults)


async def _bytes(*chunks: bytes):
    for c in chunks:
        yield c


# ---------------------------------------------------------------------------
# NSDPath + file ops
# ---------------------------------------------------------------------------


class TestNSDPath:
    def test_classified_path_shape(self):
        p = NSDPath.internal_classified("c", "d", "m", "tg", "i", "test_report", "slug", 2)
        assert p.to_unc() == "\\\\share\\hilda\\internal\\c\\d\\m\\tg\\i\\test_report\\slug\\rev2"

    def test_relative_roundtrip_is_persisted_form(self):
        # to_relative()/from_relative() are the persisted local_nsd_path form
        # (mount-root-independent per [D-013] alignment 2026-06-11).
        p = NSDPath.internal_classified("c", "d", "m", "tg", "i", "test_report", "slug", 2)
        assert p.to_relative() == "internal/c/d/m/tg/i/test_report/slug/rev2"
        assert NSDPath.from_relative(p.to_relative()) == p

    def test_from_relative_rejects_absolute(self):
        with pytest.raises(PipelineError) as exc:
            NSDPath.from_relative("/etc/passwd")
        assert exc.value.code_id == "STR-E004"

    def test_unc_roundtrip_diagnostic_api(self):
        p = NSDPath.inbound_drop("c", "d", "m", "i")
        assert NSDPath.from_unc(p.to_unc()) == p

    def test_from_unc_rejects_foreign_share(self):
        with pytest.raises(PipelineError) as exc:
            NSDPath.from_unc("\\\\other\\share\\x")
        assert exc.value.code_id == "STR-E004"

    def test_staged_classification_has_no_doc_type_segment(self):
        p = NSDPath.internal_staged_classification("c", "d", "m", "tg", "i", "f.pdf")
        assert "_staged_classification" in p.segments
        assert "test_report" not in p.segments

    def test_default_workitem_carries_inferred_tg(self):
        p = NSDPath.internal_default_workitem("c", "d", "m", "tg-sw", "f.pdf")
        assert p.segments[4] == "tg-sw" and "_unrouted" in p.segments

    def test_ingress_folder_inbound_only(self):
        p = NSDPath.ingress_folder("c", "NSD2", "deliverables/q3")
        assert p.segments[:2] == ("inbound", "nsd2")


class TestFileOps:
    async def test_write_read_hash_roundtrip(self):
        p = NSDPath.inbound_drop("c", "d", "m", "i")
        target = NSDPath(p.segments + ("file.txt",))
        await write_file(target, _bytes(b"hello ", b"world"))
        data = b"".join([chunk async for chunk in read_file(target)])
        assert data == b"hello world"
        digest = await compute_file_hash(target)
        assert len(digest) == 64

    async def test_write_idempotent_and_no_partial_left(self, tmp_path):
        target = NSDPath(("inbound", "c", "d", "m", "i", "f.txt"))
        await write_file(target, _bytes(b"same"))
        await write_file(target, _bytes(b"same"))
        files = list(target.to_local().parent.iterdir())
        assert [f.name for f in files] == ["f.txt"]

    async def test_read_missing_raises_e004(self):
        with pytest.raises(PipelineError) as exc:
            async for _ in read_file(NSDPath(("inbound", "c", "d", "m", "i", "nope.txt"))):
                pass
        assert exc.value.code_id == "STR-E004"

    async def test_list_inbound_drops(self):
        for name in ("b.txt", "a.txt"):
            await write_file(NSDPath(("inbound", "c", "d", "m", "i", name)), _bytes(b"x"))
        drops = await list_inbound_drops("c", "d", "m", "i")
        assert [p.segments[-1] for p in drops] == ["a.txt", "b.txt"]

    async def test_extract_first_page_txt_and_pending_pdf(self):
        txt = NSDPath(("inbound", "c", "d", "m", "i", "note.txt"))
        await write_file(txt, _bytes(b"first page text"))
        assert "first page" in await extract_first_page(txt)
        pdf = NSDPath(("inbound", "c", "d", "m", "i", "doc.pdf"))
        await write_file(pdf, _bytes(b"%PDF"))
        with pytest.raises(PipelineError) as exc:
            await extract_first_page(pdf)
        assert "D-011" in str(exc.value)


# ---------------------------------------------------------------------------
# Document index
# ---------------------------------------------------------------------------


class TestDocumentIndex:
    async def test_add_idempotent_first_write_wins(self):
        await add_document_index_row(make_doc())
        await add_document_index_row(make_doc(ingest_source=IngestSource.CORPORATE_PLM))
        row = await get_document_index_row_by_hash(HASH_A)
        assert row is not None and row.ingest_source == IngestSource.EMAIL

    async def test_slug_lookup(self):
        await add_document_index_row(make_doc())
        row = await get_document_index_row_by_slug("ms-1", "report-a", 1)
        assert row is not None and row.file_hash == HASH_A
        assert await get_document_index_row_by_slug("ms-1", "report-a", 9) is None

    async def test_find_doc_id_slugs_for_item_joins_associations(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc())
        slugs = await find_doc_id_slugs_for_item("item-1", DocType.TEST_REPORT)
        assert slugs == ["report-a"]
        assert await find_doc_id_slugs_for_item("item-1", DocType.WAIVER) == []

    async def test_list_revisions_ordered(self):
        await add_document_index_row(make_doc())
        await add_document_index_row(make_doc(file_hash=HASH_B, rev=2))
        revs = await list_revisions("ms-1", "report-a")
        assert [r.rev_number for r in revs] == [1, 2]

    async def test_update_review_findings_unknown_raises_e002(self):
        with pytest.raises(PipelineError) as exc:
            await update_review_findings("f" * 64, None, None)
        assert exc.value.code_id == "STR-E002"

    # --- staged-fill nullability (architect patch 2026-06-11) ---

    def test_document_index_row_accepts_null_slug_and_rev(self):
        row = make_doc(slug=None, rev=None)
        assert row.doc_id_slug is None and row.rev_number is None

    async def test_partial_unique_index_allows_multiple_null_slug_rows_same_milestone(self):
        await add_document_index_row(make_doc(file_hash=HASH_A, slug=None, rev=None))
        await add_document_index_row(make_doc(file_hash=HASH_B, slug=None, rev=None))
        assert await get_document_index_row_by_hash(HASH_A) is not None
        assert await get_document_index_row_by_hash(HASH_B) is not None

    async def test_partial_unique_index_blocks_duplicate_populated_triple(self):
        await add_document_index_row(make_doc(file_hash=HASH_A, slug="abc", rev=1))
        with pytest.raises(PipelineError) as exc:
            await add_document_index_row(make_doc(file_hash=HASH_B, slug="abc", rev=1))
        assert exc.value.code_id == "STR-E001"  # IntegrityError surfaces via session wrapper

    async def test_list_revisions_excludes_null_slug_rows(self):
        await add_document_index_row(make_doc(file_hash=HASH_A, slug="report-a", rev=1))
        await add_document_index_row(make_doc(file_hash=HASH_B, slug=None, rev=None))
        revs = await list_revisions("ms-1", "report-a")
        assert [r.file_hash for r in revs] == [HASH_A]

    async def test_set_is_final_clears_siblings(self):
        await add_document_index_row(make_doc())
        await add_document_index_row(make_doc(file_hash=HASH_B, rev=2))
        await set_is_final(HASH_A, True)
        await set_is_final(HASH_B, True)
        a = await get_document_index_row_by_hash(HASH_A)
        b = await get_document_index_row_by_hash(HASH_B)
        assert a is not None and a.is_final is False
        assert b is not None and b.is_final is True


# ---------------------------------------------------------------------------
# Associations + fan-out + reassignment
# ---------------------------------------------------------------------------


class TestAssociations:
    async def test_add_idempotent(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc())
        await add_document_item_association(make_assoc())
        assert len(await list_associations_for_file(HASH_A)) == 1

    async def test_cross_milestone_raises_e005(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc())
        with pytest.raises(PipelineError) as exc:
            await add_document_item_association(make_assoc(item="item-9", milestone="ms-2"))
        assert exc.value.code_id == "STR-E005"

    async def test_delete_removes_only_this_items_copy(self):
        await add_document_index_row(make_doc())
        a1, a2 = make_assoc(item="item-1"), make_assoc(item="item-2")
        # distinct physical copies per item per [D-055]
        p1, p2 = NSDPath.from_relative(a1.local_nsd_path), NSDPath.internal_classified(
            "carrier-a", "device-x", "ms-1", "tg-hw", "item-2", "test_report", "report-a", 1)
        a2 = a2.model_copy(update={"local_nsd_path": p2.to_relative()})
        await write_file(NSDPath(p1.segments + ("f.bin",)), _bytes(b"x"))
        await write_file(NSDPath(p2.segments + ("f.bin",)), _bytes(b"x"))
        a1 = a1.model_copy(update={"local_nsd_path": NSDPath(p1.segments + ("f.bin",)).to_relative()})
        a2 = a2.model_copy(update={"local_nsd_path": NSDPath(p2.segments + ("f.bin",)).to_relative()})
        await add_document_item_association(a1)
        await add_document_item_association(a2)

        await delete_document_item_association(HASH_A, "item-1", delete_file=True)
        assert not NSDPath.from_relative(a1.local_nsd_path).to_local().exists()
        assert NSDPath.from_relative(a2.local_nsd_path).to_local().exists()
        # index row survives (orphan cleanup is a diagnostic concern, STR-W005)
        assert await get_document_index_row_by_hash(HASH_A) is not None

    async def test_fan_out_case_a_one_owner_n_items(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc(item="item-1"))
        await add_document_item_association(make_assoc(item="item-2"))
        targets = await fan_out_plm_associations(HASH_A)
        assert len(targets) == 1
        assert targets[0].item_count == 2

    async def test_fan_out_case_b_two_owners(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc(item="item-1", owner="o1@corp", plm="PLM-1"))
        await add_document_item_association(make_assoc(item="item-2", owner="o2@corp", plm="PLM-2"))
        targets = await fan_out_plm_associations(HASH_A)
        assert len(targets) == 2

    async def test_plm_attachment_replicates_within_owner_pair(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc(item="item-1"))
        await add_document_item_association(make_assoc(item="item-2"))
        await update_association_plm_attachment(HASH_A, "item-1", "ATT-9", NOW)
        assocs = await list_associations_for_file(HASH_A)
        assert all(a.plm_attachment_id == "ATT-9" for a in assocs)

    async def test_reassign_document_to_workitem(self):
        await add_document_index_row(make_doc(routing_resolution=RoutingResolution.STAGED_DEFAULT,
                                              inferred_tg_name="tg-hw"))
        source_path = NSDPath.internal_default_workitem("carrier-a", "device-x", "ms-1",
                                                        "tg-hw", "Report A.xlsx")
        await write_file(source_path, _bytes(b"content"))
        await add_document_item_association(make_assoc(
            item="default-item", plm=None,
            local_nsd_path=source_path.to_relative(), nsd_path_type=NSDPathType.UNROUTED))

        # Target-item attributes are caller-resolved from SP (architect 2026-06-11).
        await reassign_document_to_workitem(
            HASH_A, "default-item", "item-7", "pm-42",
            target_tg_name="tg-sw", target_owner_corp_id="ops.member", target_owner_corp_usa_email="o7@corp", target_plm_id="PLM-7")

        assocs = await list_associations_for_file(HASH_A)
        assert [a.delivery_item_id for a in assocs] == ["item-7"]
        assert assocs[0].associated_by == "pm-42"
        assert not source_path.to_local().exists()
        assert NSDPath.from_relative(assocs[0].local_nsd_path).to_local().exists()
        doc = await get_document_index_row_by_hash(HASH_A)
        assert doc is not None
        assert doc.routing_resolution == RoutingResolution.TPM_REASSIGNED
        assert doc.inferred_tg_name == "tg-sw"
        audit = await query_communications(action_type="reassign_to_workitem")
        assert len(audit) == 1 and audit[0].credential_id == "pm-42"


# ---------------------------------------------------------------------------
# FR-87 TPM staged-document resolution (steps B + C)
# ---------------------------------------------------------------------------


async def _stage_doc(file_hash=HASH_A, nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED,
                     doc_type=DocType.UNRESOLVED, slug=None, rev=None, item="item-1",
                     tg="tg-hw", filename="Report A.xlsx"):
    """Create a DocumentIndexRow + association at a staged NSD path, with the file present."""
    await add_document_index_row(make_doc(file_hash=file_hash, slug=slug, rev=rev,
                                          doc_type=doc_type, original_filename=filename))
    if nsd_path_type == NSDPathType.STAGED_NOT_CLASSIFIED:
        path = NSDPath.internal_staged_classification("carrier-a", "device-x", "ms-1", tg, item, filename)
    else:  # STAGED_NOT_REVISION
        path = NSDPath.internal_staged_revision("carrier-a", "device-x", "ms-1", tg, item,
                                                doc_type.value, filename)
    await write_file(NSDPath(path.segments + ()), _bytes(b"staged content"))
    await add_document_item_association(make_assoc(
        file_hash=file_hash, item=item, plm=None,
        local_nsd_path=path.to_relative(), nsd_path_type=nsd_path_type))
    return path


class TestTpmResolveDocType:
    async def test_resolved_branch_moves_to_classified(self):
        src = await _stage_doc()
        await tpm_resolve_doc_type(HASH_A, "item-1", DocType.TEST_REPORT,
                                   doc_id_slug="report-a", rev_number=1, pm_id="pm-9")
        assoc = (await list_associations_for_file(HASH_A))[0]
        assert assoc.nsd_path_type == NSDPathType.CLASSIFIED
        assert "test_report/report-a/rev1" in assoc.local_nsd_path
        assert not src.to_local().exists()
        assert NSDPath.from_relative(assoc.local_nsd_path).to_local().exists()
        doc = await get_document_index_row_by_hash(HASH_A)
        assert (doc.doc_type, doc.doc_id_slug, doc.rev_number) == (DocType.TEST_REPORT, "report-a", 1)
        audit = await query_communications(action_type="tpm_resolve_doc_type")
        assert len(audit) == 1 and audit[0].credential_id == "pm-9"

    async def test_ambiguous_branch_moves_to_staged_revision(self):
        await _stage_doc()
        await tpm_resolve_doc_type(HASH_A, "item-1", DocType.WAIVER, pm_id="pm-9")
        assoc = (await list_associations_for_file(HASH_A))[0]
        assert assoc.nsd_path_type == NSDPathType.STAGED_NOT_REVISION
        assert "_staged_revision" in assoc.local_nsd_path
        doc = await get_document_index_row_by_hash(HASH_A)
        assert doc.doc_type == DocType.WAIVER
        assert doc.doc_id_slug is None and doc.rev_number is None

    async def test_asymmetric_null_raises_e010(self):
        await _stage_doc()
        with pytest.raises(PipelineError) as exc:
            await tpm_resolve_doc_type(HASH_A, "item-1", DocType.TEST_REPORT,
                                       doc_id_slug="report-a", pm_id="pm-9")  # rev omitted
        assert exc.value.code_id == "STR-E010"

    async def test_state_mismatch_raises_e009(self):
        # File at CLASSIFIED (not the staged_not_classification source) → E009
        await add_document_index_row(make_doc(slug="report-a", rev=1))
        await add_document_item_association(make_assoc())  # CLASSIFIED
        with pytest.raises(PipelineError) as exc:
            await tpm_resolve_doc_type(HASH_A, "item-1", DocType.TECH_REPORT, pm_id="pm-9")
        assert exc.value.code_id == "STR-E009"

    async def test_idempotent_recall_warns_w008(self):
        await _stage_doc()
        await tpm_resolve_doc_type(HASH_A, "item-1", DocType.TEST_REPORT,
                                   doc_id_slug="report-a", rev_number=1, pm_id="pm-9")
        # second call at target state → no-op + W008 audit row, no error
        await tpm_resolve_doc_type(HASH_A, "item-1", DocType.TEST_REPORT,
                                   doc_id_slug="report-a", rev_number=1, pm_id="pm-9")
        w008 = [c for c in await query_communications(delivery_item_id="item-1")
                if c.summary and "STR-W008" in c.summary]
        assert len(w008) == 1


class TestTpmResolveRevision:
    async def test_new_assigns_slug_from_filename_rev1(self):
        src = await _stage_doc(nsd_path_type=NSDPathType.STAGED_NOT_REVISION,
                               doc_type=DocType.TEST_REPORT, filename="Battery Test.xlsx")
        await tpm_resolve_revision(HASH_A, "item-1", RevisionResolution.new(), pm_id="pm-3")
        assoc = (await list_associations_for_file(HASH_A))[0]
        assert assoc.nsd_path_type == NSDPathType.CLASSIFIED
        doc = await get_document_index_row_by_hash(HASH_A)
        assert doc.doc_id_slug == "battery-test-xlsx" and doc.rev_number == 1
        assert not src.to_local().exists()
        audit = await query_communications(action_type="tpm_resolve_revision")
        assert len(audit) == 1 and "NEW" in audit[0].summary

    async def test_revision_of_computes_next_rev(self):
        # existing rev1 in family + a staged doc resolved as revision_of → rev2
        await add_document_index_row(make_doc(file_hash=HASH_B, slug="report-a", rev=1,
                                              doc_type=DocType.TEST_REPORT))
        await _stage_doc(file_hash=HASH_A, nsd_path_type=NSDPathType.STAGED_NOT_REVISION,
                         doc_type=DocType.TEST_REPORT)
        await tpm_resolve_revision(HASH_A, "item-1",
                                   RevisionResolution.revision_of("report-a"), pm_id="pm-3")
        doc = await get_document_index_row_by_hash(HASH_A)
        assert doc.doc_id_slug == "report-a" and doc.rev_number == 2
        audit = await query_communications(action_type="tpm_resolve_revision")
        assert "REVISION_OF" in audit[0].summary

    async def test_state_mismatch_raises_e009(self):
        await _stage_doc(nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED)  # wrong source state
        with pytest.raises(PipelineError) as exc:
            await tpm_resolve_revision(HASH_A, "item-1", RevisionResolution.new(), pm_id="pm-3")
        assert exc.value.code_id == "STR-E009"

    async def test_idempotent_recall_warns_w008(self):
        await _stage_doc(nsd_path_type=NSDPathType.STAGED_NOT_REVISION, doc_type=DocType.TEST_REPORT)
        await tpm_resolve_revision(HASH_A, "item-1", RevisionResolution.new(), pm_id="pm-3")
        await tpm_resolve_revision(HASH_A, "item-1", RevisionResolution.new(), pm_id="pm-3")
        w008 = [c for c in await query_communications(delivery_item_id="item-1")
                if c.summary and "STR-W008" in c.summary]
        assert len(w008) == 1


class TestRevisionResolution:
    def test_new_factory(self):
        r = RevisionResolution.new()
        assert r.kind == "new" and r.revised_doc_id_slug is None

    def test_revision_of_factory(self):
        r = RevisionResolution.revision_of("slug-x")
        assert r.kind == "revision_of" and r.revised_doc_id_slug == "slug-x"

    def test_revision_of_requires_slug(self):
        with pytest.raises(ValueError):
            RevisionResolution(kind="revision_of")

    def test_new_rejects_slug(self):
        with pytest.raises(ValueError):
            RevisionResolution(kind="new", revised_doc_id_slug="x")


# ---------------------------------------------------------------------------
# Download tokens
# ---------------------------------------------------------------------------


class TestDownloadTokens:
    async def test_roundtrip(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc())
        token = await make_download_token(HASH_A, "item-1")
        file_hash, item_id, path = await resolve_download_token(token)
        assert (file_hash, item_id) == (HASH_A, "item-1")
        assert isinstance(path, NSDPath)

    async def test_expired_raises_e007(self):
        await add_document_index_row(make_doc())
        await add_document_item_association(make_assoc())
        token = await make_download_token(HASH_A, "item-1", ttl_seconds=-1)
        with pytest.raises(PipelineError) as exc:
            await resolve_download_token(token)
        assert exc.value.code_id == "STR-E007"

    async def test_tampered_raises_e007(self):
        token = await make_download_token(HASH_A, "item-1")
        with pytest.raises(PipelineError) as exc:
            await resolve_download_token(token[:-4] + "AAAA")
        assert exc.value.code_id == "STR-E007"


# ---------------------------------------------------------------------------
# Redis: cache TTL cap + batch idempotency
# ---------------------------------------------------------------------------


class TestRedis:
    async def test_cache_roundtrip_and_delete_idempotent(self):
        await cache_set("k", b"v", ttl_seconds=60)
        assert await cache_get("k") == b"v"
        await cache_delete("k")
        await cache_delete("k")  # no-op
        assert await cache_get("k") is None

    async def test_ttl_cap_raises_e008(self):
        with pytest.raises(PipelineError) as exc:
            await cache_set("k", b"v", ttl_seconds=86_401)
        assert exc.value.code_id == "STR-E008"

    async def test_batch_idempotency(self):
        assert await check_batch_idempotency("B-1", 3) is None
        await record_batch_idempotency(BatchIdempotencyKey(batch_id="B-1", item_index=3, status="done"))
        assert await check_batch_idempotency("B-1", 3) == "done"


# ---------------------------------------------------------------------------
# Overrides / folder routing / tags / comm log
# ---------------------------------------------------------------------------


def make_override(**kw) -> AutomationRuleOverride:
    defaults = dict(
        scope=RuleScope.DEVICE, scope_id="dev-1", rule_id="rule-1",
        parameter_name="interval_minutes", parameter_value="15",
        set_by_pm_id="pm-1", set_at=NOW, expires_at=None,
    )
    defaults.update(kw)
    return AutomationRuleOverride(**defaults)


class TestOverrides:
    async def test_set_get_clear_with_audit(self):
        await set_override(make_override())
        active = await get_active_overrides(RuleScope.DEVICE, "dev-1", "rule-1")
        assert len(active) == 1 and active[0].parameter_value == "15"
        await clear_override(RuleScope.DEVICE, "dev-1", "rule-1", "interval_minutes", pm_id="pm-1")
        assert await get_active_overrides(RuleScope.DEVICE, "dev-1", "rule-1") == []
        log = await query_communications(pm_id="pm-1")
        assert {r.action_type for r in log} == {"set_override", "clear_override"}

    async def test_expired_overrides_filtered(self):
        await set_override(make_override(expires_at=NOW - timedelta(days=400)))
        assert await get_active_overrides(RuleScope.DEVICE, "dev-1", "rule-1") == []

    async def test_global_scope_null_scope_id(self):
        await set_override(make_override(scope=RuleScope.GLOBAL, scope_id=None))
        active = await list_active_overrides(scope=RuleScope.GLOBAL)
        assert len(active) == 1 and active[0].scope_id is None

    async def test_list_all_override_rule_ids(self):
        await set_override(make_override())
        await set_override(make_override(rule_id="rule-2"))
        assert await list_all_override_rule_ids() == {"rule-1", "rule-2"}


class TestFolderRouting:
    async def test_replace_all_and_unknown_item_no_e006(self):
        # valid_item_nos is caller-resolved from SP (architect 2026-06-11)
        rows = [TGFolderRoutingRow(milestone_id="ms-1", tg_name="tg-hw",
                                   ingress_folder="q3/reports", item_no=1)]
        await set_folder_routing_for_tg("ms-1", "tg-hw", rows, valid_item_nos={1, 2})
        assert len(await get_folder_routing_for_tg("ms-1", "tg-hw")) == 1
        # replace-all
        await set_folder_routing_for_tg("ms-1", "tg-hw", [], valid_item_nos={1, 2})
        assert await get_folder_routing_for_tg("ms-1", "tg-hw") == []
        with pytest.raises(PipelineError) as exc:
            await set_folder_routing_for_tg("ms-1", "tg-hw", [
                TGFolderRoutingRow(milestone_id="ms-1", tg_name="tg-hw",
                                   ingress_folder="x", item_no=42)], valid_item_nos={1, 2})
        assert exc.value.code_id == "STR-E006"


class TestTagCatalog:
    async def test_upsert_deactivate_reactivate(self):
        await upsert_tag(TagCatalogRow(customer_id="cust", tag="MUST_HAVE"))
        await upsert_tag(TagCatalogRow(customer_id="cust", tag="RegA"))
        assert await get_tag_catalog("cust") == {"MUST_HAVE", "RegA"}
        await deactivate_tag("cust", "RegA")
        assert await get_tag_catalog("cust") == {"MUST_HAVE"}
        await reactivate_tag("cust", "RegA")
        assert await get_tag_catalog("cust") == {"MUST_HAVE", "RegA"}


class TestCommunicationLog:
    async def test_append_and_filtered_query(self):
        for i, channel in enumerate((Channel.EMAIL, Channel.CORPORATE_PLM)):
            await log_communication(CommunicationLogRow(
                log_id=f"log-{i}", channel=channel, direction=Direction.INBOUND,
                timestamp=NOW + timedelta(minutes=i), delivery_item_id="item-1",
                action_type="submission",
                attachments=[{"filename": "f.bin", "file_hash": HASH_A}] if i == 0 else [],
            ))
        assert len(await query_communications(delivery_item_id="item-1")) == 2
        assert len(await query_communications(channel=Channel.EMAIL)) == 1
        by_hash = await query_communications(file_hash=HASH_A)
        assert len(by_hash) == 1 and by_hash[0].log_id == "log-0"
        # DESC ordering
        rows = await query_communications(delivery_item_id="item-1")
        assert rows[0].log_id == "log-1"


# ---------------------------------------------------------------------------
# Registrations + CLI smoke
# ---------------------------------------------------------------------------


class TestRegistrations:
    def test_str_error_codes_registered(self):
        for code in ("STR-E001", "STR-E005", "STR-E006", "STR-E007", "STR-E008",
                     "STR-W002", "STR-W004", "STR-W005", "STR-W006", "STR-W007"):
            assert code in ERROR_CODES, code

    def test_qc_template_registered(self):
        assert "STR:schema_roundtrip" in QC_REGISTRY


class TestCli:
    async def test_mock_cycle_green(self, capsys):
        from core.src.storage import storage_cli

        code = await storage_cli._cmd_mock("run-test")
        out = capsys.readouterr().out
        assert "RPT|STR|run-test" in out
        assert "ops_fail=0" in out
        assert code == 0
