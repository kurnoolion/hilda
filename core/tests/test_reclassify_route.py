"""RECLASS-4 (2026-08-24) — POST /browse/{c}/{d}/{m}/tg/{tg}/reclassify.

TestClient smoke coverage for the FR-87 step B UI wiring:
  * invalid_doc_type -> 303 + outcome=invalid_doc_type flash
  * no_doc_row -> 303 + outcome=no_doc_row flash
  * happy path: seed staged doc + POST -> 303 + outcome=reclassified,
    document_index.doc_type upgraded, association nsd_path_type flipped
    to CLASSIFIED, NSD file moved to new doc-type folder.
"""
from __future__ import annotations

from datetime import datetime, timezone

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from core.src.dashboard import DashboardConfig, build_app
from core.src.storage import configure_engine, init_db, set_redis_client, write_file
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.db import (
    DocumentIndexTable, DocumentItemAssociationTable, session_scope,
)
from core.src.storage.models import NSDPathType


pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

# python-multipart required for Form parsing.
pytest.importorskip(
    "multipart",
    reason="python-multipart not installed; skipping POST route tests",
)


NOW = datetime.now(timezone.utc)
CUSTOMER = "MMK"
DEVICE = "SM-S671U1"
MILESTONE = "P1"
TG = "HW PL"
ITEM_ID = f"{CUSTOMER}-{DEVICE}-{MILESTONE}-42"
FILE_HASH = "c" * 64


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


async def _seed_staged_unresolved_doc(filename: str = "hac_report.pdf") -> None:
    """Land bytes + document_index (doc_type=unresolved, no slug) +
    document_item_association (nsd_path_type=STAGED_NOT_CLASSIFIED) matching
    what NSD/PLM/Email ingest would produce when doc_type classification
    misses. Path shape mirrors NSDPath.internal_staged_classification which
    is what the router chooses for STAGED_NOT_CLASSIFIED per FR-86 matrix.
    """
    from core.src.storage.nsd import NSDPath
    src_path = NSDPath.internal_staged_classification(
        CUSTOMER, DEVICE, MILESTONE, TG, "42", filename,
    )
    await write_file(src_path, _bytes(b"payload-bytes"))

    async with session_scope() as session:
        session.add(DocumentIndexTable(
            file_hash=FILE_HASH,
            milestone_id=MILESTONE,
            customer_id=CUSTOMER,
            device_id=DEVICE,
            doc_type="unresolved",
            doc_id_slug=None,
            rev_number=None,
            ingest_source="NetworkSharedDrive",
            original_filename=filename,
            inferred_tg_name=TG,
            routing_resolution="SubstringMatch",
            ingested_at=NOW,
        ))
        session.add(DocumentItemAssociationTable(
            file_hash=FILE_HASH,
            delivery_item_id=ITEM_ID,
            milestone_id=MILESTONE,
            local_nsd_path=src_path.to_relative(),
            nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
            owner_corp_id="",
            associated_at=NOW,
        ))
        await session.commit()


class TestReclassifyRoute:

    def _post(self, cfg, **form):
        client = TestClient(build_app(cfg))
        return client.post(
            f"/browse/{CUSTOMER}/{DEVICE}/{MILESTONE}/tg/{TG}/reclassify",
            data=form,
            follow_redirects=False,
        )

    async def test_invalid_doc_type_flashes_error(self, cfg):
        r = self._post(cfg, file_hash=FILE_HASH, new_doc_type="not_a_real_type")
        assert r.status_code == 303
        assert "outcome=invalid_doc_type" in r.headers["location"]

    async def test_no_doc_row_flashes_error(self, cfg):
        # No seed -- document_index row doesn't exist for this hash.
        r = self._post(cfg, file_hash=FILE_HASH, new_doc_type="test_report")
        assert r.status_code == 303
        assert "outcome=no_doc_row" in r.headers["location"]

    async def test_happy_path_reclassifies_and_moves(self, cfg):
        """Happy path: seed a staged Unresolved doc + POST reclassify to
        test_report. Verify: (a) 303 outcome=reclassified, (b) document_index
        row upgraded (doc_type=test_report + slug + rev populated), (c)
        association promoted to CLASSIFIED, (d) local_nsd_path now includes
        the new doc-type segment.
        """
        await _seed_staged_unresolved_doc()

        r = self._post(cfg, file_hash=FILE_HASH, new_doc_type="test_report")
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "outcome=reclassified" in loc
        assert "doc_type=test_report" in loc

        async with session_scope() as session:
            di = await session.get(DocumentIndexTable, FILE_HASH)
            assert di is not None
            assert di.doc_type == "test_report"
            assert di.doc_id_slug  # non-empty; derived from filename
            assert di.rev_number == 1

            assoc = await session.get(
                DocumentItemAssociationTable, (FILE_HASH, ITEM_ID),
            )
            assert assoc is not None
            assert assoc.nsd_path_type == NSDPathType.CLASSIFIED.value
            # New path lives under the concrete doc_type folder now
            assert "test_report" in assoc.local_nsd_path.lower()
