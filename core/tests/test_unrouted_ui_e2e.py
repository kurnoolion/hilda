"""UR-9 (Ph-2 2026-08-01) -- end-to-end trace through UR-1..8.

Walks the whole manual-routing flow against an in-memory SQLite +
TestClient dashboard:

  1. Seed: create a delivery item (route target) + a document_index row
     with NO association (the "unrouted" state).
  2. Landing GET shows the _unknownTG bucket with a count=1 badge.
  3. /_unknownTG/ GET lists the orphan file + the target-item dropdown.
  4. POST /_unknownTG/route with the file_hash + target -> 303 back to
     the /_unknownTG/ page with outcome=routed in the query params.
  5. Follow the redirect -> banner rendered.
  6. Landing GET after route: badge is gone (count back to zero).
  7. Second GET /_unknownTG/: doc is no longer listed.

Verifies the storage-side helpers (list_unrouted, count_unrouted,
route_unrouted_to_item) + route wiring + template flash region + landing
badge decrement all work together, not just in isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from core.src.dashboard import DashboardConfig, build_app
from core.src.storage import (
    add_document_index_row, configure_engine, init_db, set_redis_client,
    write_file,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.delivery_item_ops import create_delivery_item
from core.src.storage.models import DocumentIndexRow, RoutingResolution
from core.src.storage.nsd import NSDPath
from core.src.template_schema import DeliveryItemBase, DocType, IngestSource


pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

# python-multipart required for the POST step (Form parsing). Skip the
# whole file when not present -- storage-only tests still cover their
# side in test_unrouted_ops.py.
pytest.importorskip(
    "multipart",
    reason="python-multipart not installed; skipping POST route tests",
)


NOW = datetime.now(timezone.utc)
HASH_A = "a" * 64
CUSTOMER = "MMK"
DEVICE = "SM-A012U"
MILESTONE = "DRR"
TARGET_ITEM_ID = f"{CUSTOMER}-{DEVICE}-{MILESTONE}-5"


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


@pytest.fixture
def cfg():
    return DashboardConfig(
        mock_auth=True, ph1_minimal=False,
        wopi_jwt_secret="test-secret-abcdef1234567890",
        onlyoffice_public_url="http://oo.test/office",
    )


async def _bytes(*chunks: bytes):
    for c in chunks:
        yield c


async def _seed_unrouted_and_target(filename="orphan.pdf"):
    """Insert the unrouted doc row + a valid target work item + the on-disk
    source file (write_file). Returns nothing -- the fixture identifiers are
    module-level constants."""
    await add_document_index_row(DocumentIndexRow(
        file_hash=HASH_A, milestone_id=MILESTONE,
        customer_id=CUSTOMER, device_id=DEVICE,
        doc_type=DocType.TEST_REPORT,
        doc_id_slug=None, rev_number=None,
        ingest_source=IngestSource.EMAIL,
        original_filename=filename,
        routing_resolution=RoutingResolution.STAGED_DEFAULT,
        ingested_at=NOW,
    ))
    await create_delivery_item(DeliveryItemBase(
        item_id=TARGET_ITEM_ID, item_no=5, milestone_id=MILESTONE,
        customer_id=CUSTOMER, device_id=DEVICE,
        item_name="Deliverable X", item_type="test_tech_waiver_report",
        tg_name="CPM", delivery_state="Open",
        tracking_modality=["Email"], no_customer_upload=False,
        last_updated=NOW, sort_order=5, path_id="item-5",
        force_tracking_enabled=True, owner_corp_id="owner-1",
        item_path_id="item_5", tg_path_id="CPM",
    ))
    src = NSDPath.internal_default_workitem(
        CUSTOMER, DEVICE, MILESTONE, "_unknown_tg", filename,
    )
    await write_file(src, _bytes(b"payload"))


class TestUnroutedEndToEnd:
    async def test_full_route_flow(self, cfg):
        await _seed_unrouted_and_target()
        client = TestClient(build_app(cfg))

        # --- Landing shows the badge ---
        r = client.get(f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/")
        assert r.status_code == 200
        assert "1 unrouted" in r.text, r.text[-800:]
        assert f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/_unknownTG/" in r.text

        # --- /_unknownTG/ lists the orphan + dropdown ---
        r = client.get(f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/_unknownTG/")
        assert r.status_code == 200
        assert "orphan.pdf" in r.text
        assert TARGET_ITEM_ID in r.text        # dropdown option value
        assert HASH_A in r.text                # hidden input

        # --- POST commits the route ---
        r = client.post(
            f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/_unknownTG/route",
            data={
                "file_hash": HASH_A,
                "target_delivery_item_id": TARGET_ITEM_ID,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "outcome=routed" in r.headers["location"]

        # --- Follow redirect renders flash banner ---
        r = client.get(r.headers["location"])
        assert r.status_code == 200
        assert "Routed to" in r.text and TARGET_ITEM_ID in r.text

        # --- Landing count is back to zero. Route landed the file in the
        # internal staged_classification NSD tree, not the view-tree; both
        # tg_entries AND unrouted_count are now zero, so the landing falls
        # through to the empty-state message per UR-7 template contract.
        r = client.get(f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/")
        assert r.status_code == 200
        assert "1 unrouted" not in r.text
        assert "No documents received yet" in r.text

        # --- /_unknownTG/ page shows the no-unrouted empty state ---
        r = client.get(f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/_unknownTG/")
        assert r.status_code == 200
        assert "No unrouted files" in r.text
        assert "orphan.pdf" not in r.text
