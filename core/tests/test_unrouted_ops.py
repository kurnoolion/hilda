"""test_unrouted_ops.py -- UR-3 storage helpers for the /_unknownTG UI.

Uses the same fixture pattern as test_storage.py -- fresh in-memory SQLite
per test, NSD mount rooted in tmp_path so the file-move happens against a
real (transient) filesystem.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage import (
    add_document_index_row, add_document_item_association,
    configure_engine, init_db, set_redis_client, write_file,
)
from core.src.storage.delivery_item_ops import create_delivery_item
from core.src.storage.models import (
    DocumentIndexRow, DocumentItemAssociation, NSDPathType, RoutingResolution,
)
from core.src.storage.nsd import NSDPath
from core.src.template_schema import DocType, IngestSource
from core.src.storage.nsd import NSDPath
from core.src.storage.unrouted_ops import (
    count_unrouted_for_scope,
    list_all_unrouted_scopes,
    list_route_candidates_for_scope,
    list_unrouted_for_scope,
    route_unrouted_to_item,
)


NOW = datetime.now(timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


async def _bytes(*chunks: bytes):
    """AsyncIterable[bytes] wrapper for write_file() -- matches test_storage.py pattern."""
    for c in chunks:
        yield c


@pytest.fixture(autouse=True)
async def _fresh_env(tmp_path):
    """Per-test in-mem DB + fakeredis + NSD rooted in tmp_path."""
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


def _mk_doc(
    *, file_hash: str, milestone: str = "DRR",
    customer: str = "MMK", device: str = "SM-A012U",
    filename: str = "report.pdf",
    resolution: RoutingResolution = RoutingResolution.STAGED_DEFAULT,
) -> DocumentIndexRow:
    """Default resolution=STAGED_DEFAULT (fell through to Default WI) --
    what live unrouted files carry. "Unrouted" for the UI is defined by
    absence of association, not by any particular routing_resolution."""
    return DocumentIndexRow(
        file_hash=file_hash, milestone_id=milestone,
        customer_id=customer, device_id=device,
        doc_type=DocType.TEST_REPORT,
        doc_id_slug=None, rev_number=None,
        ingest_source=IngestSource.EMAIL,
        original_filename=filename,
        routing_resolution=resolution,
        ingested_at=NOW,
    )


def _mk_item(
    *, item_id: str = "MMK-SM-A012U-DRR-5",
    item_no: int = 5, customer: str = "MMK",
    device: str = "SM-A012U", milestone: str = "DRR",
    tg_name: str = "CPM", item_type: str = "test_tech_waiver_report",
    item_name: str = "Some deliverable",
    delivery_state: str = "Open",
    **overrides,
):
    """DeliveryItemBase Pydantic instance minimally satisfying validators."""
    from core.src.template_schema import DeliveryItemBase
    defaults = dict(
        item_id=item_id,
        item_no=item_no,
        milestone_id=milestone,
        customer_id=customer,
        device_id=device,
        item_name=item_name,
        item_type=item_type,
        tg_name=tg_name,
        delivery_state=delivery_state,
        tracking_modality=["Email"],
        no_customer_upload=False,
        last_updated=NOW,
        sort_order=item_no,
        path_id=f"item-{item_no}",
        force_tracking_enabled=True,
        owner_corp_id="owner-1",
        item_path_id=f"item_{item_no}",
        tg_path_id=tg_name,
    )
    defaults.update(overrides)
    return DeliveryItemBase(**defaults)  # type: ignore[arg-type]


class TestListUnrouted:
    async def test_empty_scope_returns_empty(self):
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert result == []

    async def test_returns_unrouted_in_scope(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert len(result) == 1
        assert result[0].file_hash == HASH_A
        assert result[0].original_filename == "report.pdf"
        assert result[0].is_dup_hash_elsewhere is False

    async def test_excludes_wrong_customer(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A, customer="OTHER"))
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert result == []

    async def test_excludes_wrong_device(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A, device="SM-M456U"))
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert result == []

    async def test_excludes_wrong_milestone(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A, milestone="OtherMs"))
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert result == []

    async def test_excludes_when_associated(self):
        # Doc with an association is NOT "unrouted" from the UI's PoV --
        # the routing pipeline placed it against SOME item.
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        await add_document_item_association(DocumentItemAssociation(
            file_hash=HASH_A,
            delivery_item_id="MMK-SM-A012U-DRR-99",
            milestone_id="DRR",
            local_nsd_path="internal/MMK/SM-A012U/DRR/CPM/item_99/report.pdf",
            nsd_path_type=NSDPathType.CLASSIFIED,
            owner_corp_id="owner-1",
            associated_at=NOW,
        ))
        result = await list_unrouted_for_scope("MMK", "SM-A012U", "DRR")
        assert result == []


class TestCountUnrouted:
    """UR-7 landing-badge count helper — same predicate as list, single COUNT."""

    async def test_zero_when_empty(self):
        assert await count_unrouted_for_scope("MMK", "SM-A012U", "DRR") == 0

    async def test_counts_in_scope_unrouted(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        await add_document_index_row(_mk_doc(file_hash=HASH_B))
        assert await count_unrouted_for_scope("MMK", "SM-A012U", "DRR") == 2

    async def test_excludes_associated_rows(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        await add_document_index_row(_mk_doc(file_hash=HASH_B))
        await add_document_item_association(DocumentItemAssociation(
            file_hash=HASH_A,
            delivery_item_id="MMK-SM-A012U-DRR-99",
            milestone_id="DRR",
            local_nsd_path="internal/MMK/SM-A012U/DRR/CPM/item_99/report.pdf",
            nsd_path_type=NSDPathType.CLASSIFIED,
            owner_corp_id="owner-1",
            associated_at=NOW,
        ))
        assert await count_unrouted_for_scope("MMK", "SM-A012U", "DRR") == 1

    async def test_scope_filters(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A, customer="OTHER"))
        await add_document_index_row(_mk_doc(file_hash=HASH_B, milestone="OtherMs"))
        assert await count_unrouted_for_scope("MMK", "SM-A012U", "DRR") == 0


class TestListAllUnroutedScopes:
    """UR-8: enumerate distinct (customer, device, milestone) tuples in
    document_index for the weekly ops digest scan."""

    async def test_empty(self):
        assert await list_all_unrouted_scopes() == []

    async def test_returns_distinct_scopes(self):
        # Two docs in same scope + one in another -> 2 distinct scopes
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        await add_document_index_row(_mk_doc(file_hash=HASH_B))
        await add_document_index_row(_mk_doc(
            file_hash=HASH_C, customer="OTHER", milestone="GCF",
        ))
        scopes = set(await list_all_unrouted_scopes())
        assert scopes == {
            ("MMK", "SM-A012U", "DRR"),
            ("OTHER", "SM-A012U", "GCF"),
        }


class TestListRouteCandidates:
    async def test_returns_eligible_items(self):
        await create_delivery_item(_mk_item(item_no=5, item_name="Real deliverable"))
        cands = await list_route_candidates_for_scope("MMK", "SM-A012U", "DRR")
        assert len(cands) == 1
        assert cands[0].item_no == 5

    async def test_excludes_confirmation(self):
        await create_delivery_item(_mk_item(
            item_id="MMK-SM-A012U-DRR-1", item_no=1,
            item_type="Confirmation", item_name="Confirmation item",
            no_customer_upload=True,
        ))
        cands = await list_route_candidates_for_scope("MMK", "SM-A012U", "DRR")
        assert cands == []

    async def test_excludes_default_wi(self):
        await create_delivery_item(_mk_item(
            item_id="MMK-SM-A012U-DRR-0", item_no=0,
            item_type="Default", item_name="Default WI",
            no_customer_upload=True,
        ))
        cands = await list_route_candidates_for_scope("MMK", "SM-A012U", "DRR")
        assert cands == []

    async def test_excludes_configured_item_names(self):
        await create_delivery_item(_mk_item(
            item_no=5, item_name="Special deliverable X",
        ))
        cands = await list_route_candidates_for_scope(
            "MMK", "SM-A012U", "DRR",
            excluded_item_names=["Special deliverable X"],
        )
        assert cands == []

    async def test_does_not_exclude_closed_items(self):
        """Architect ask 2026-07-31: Closed items ARE valid route targets
        (TPM may attach late doc). Do not filter by state."""
        await create_delivery_item(_mk_item(
            item_no=5, item_name="Deliverable X", delivery_state="Closed",
        ))
        cands = await list_route_candidates_for_scope("MMK", "SM-A012U", "DRR")
        assert len(cands) == 1

    async def test_scope_filter(self):
        # Same item in different scope: should not appear
        await create_delivery_item(_mk_item(
            item_id="OTHER-SM-A012U-DRR-5", customer="OTHER",
        ))
        cands = await list_route_candidates_for_scope("MMK", "SM-A012U", "DRR")
        assert cands == []


class TestRouteUnroutedToItem:
    async def _seed(self, *, filename: str = "report.pdf",
                    doc_hash: str = HASH_A) -> tuple[str, str]:
        """Create an unrouted doc row + a target item + the on-disk source
        file. Returns (file_hash, target_item_id)."""
        await add_document_index_row(_mk_doc(file_hash=doc_hash, filename=filename))
        item = _mk_item()
        await create_delivery_item(item)
        # Materialize the source file on disk so route can move it
        source_path = NSDPath.internal_default_workitem(
            "MMK", "SM-A012U", "DRR", "_unknown_tg", filename,
        )
        await write_file(source_path, _bytes(b"file bytes here"))
        return doc_hash, item.item_id

    async def test_routes_happy_path(self, tmp_path):
        file_hash, target_id = await self._seed()
        result = await route_unrouted_to_item(
            file_hash=file_hash,
            target_delivery_item_id=target_id,
            tpm_id="tpm@corp",
        )
        assert result.outcome == "routed"
        assert result.file_hash == file_hash
        assert result.target_delivery_item_id == target_id
        # Target path relative form -- staged_classification: no doc_type segment
        assert result.target_nsd_path is not None
        assert "_staged_classification" in result.target_nsd_path
        assert "report.pdf" in result.target_nsd_path

        # Doc index updated
        from core.src.storage.document_ops import get_document_index_row_by_hash
        doc = await get_document_index_row_by_hash(file_hash)
        assert doc is not None
        assert doc.routing_resolution == RoutingResolution.TPM_REASSIGNED
        assert doc.inferred_tg_name == "CPM"

        # Association created
        from core.src.storage.document_ops import list_associations_for_file
        assocs = await list_associations_for_file(file_hash)
        assert len(assocs) == 1
        assert assocs[0].delivery_item_id == target_id
        assert assocs[0].nsd_path_type == NSDPathType.STAGED_NOT_CLASSIFIED

    async def test_doc_not_found(self):
        result = await route_unrouted_to_item(
            file_hash=HASH_A, target_delivery_item_id="whatever",
            tpm_id="tpm@corp",
        )
        assert result.outcome == "doc_not_found"

    async def test_target_not_found(self):
        await add_document_index_row(_mk_doc(file_hash=HASH_A))
        result = await route_unrouted_to_item(
            file_hash=HASH_A, target_delivery_item_id="MMK-does-not-exist",
            tpm_id="tpm@corp",
        )
        assert result.outcome == "target_not_found"

    async def test_already_routed_elsewhere(self):
        file_hash, target_id = await self._seed()
        # Pre-existing association for a DIFFERENT item
        await add_document_item_association(DocumentItemAssociation(
            file_hash=file_hash,
            delivery_item_id="MMK-SM-A012U-DRR-99",
            milestone_id="DRR",
            local_nsd_path="internal/MMK/SM-A012U/DRR/CPM/item_99/report.pdf",
            nsd_path_type=NSDPathType.CLASSIFIED,
            owner_corp_id="owner-1",
            associated_at=NOW,
        ))
        result = await route_unrouted_to_item(
            file_hash=file_hash,
            target_delivery_item_id=target_id,
            tpm_id="tpm@corp",
        )
        assert result.outcome == "already_routed_elsewhere"
        assert "MMK-SM-A012U-DRR-99" in (result.error or "")

    async def test_already_routed_to_this_item_idempotent(self):
        file_hash, target_id = await self._seed()
        # Pre-existing association for the SAME item
        await add_document_item_association(DocumentItemAssociation(
            file_hash=file_hash,
            delivery_item_id=target_id,
            milestone_id="DRR",
            local_nsd_path="internal/MMK/SM-A012U/DRR/CPM/item_5/report.pdf",
            nsd_path_type=NSDPathType.CLASSIFIED,
            owner_corp_id="owner-1",
            associated_at=NOW,
        ))
        result = await route_unrouted_to_item(
            file_hash=file_hash,
            target_delivery_item_id=target_id,
            tpm_id="tpm@corp",
        )
        assert result.outcome == "already_routed_to_this_item"
