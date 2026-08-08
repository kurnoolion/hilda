"""D-155 (2026-07-26) — archive-container dispatch helpers in
workflow_engine.tasks.inbound_attachment.

Full pipeline (router+storage+view-tree) is covered end-to-end by the
existing test_workflow_engine_tasks + test_archive_extractor +
test_document_view_writer suites. This file locks the dispatch glue that
lives above the router: extension detection, hash helper, and the
ARCHIVE_CONTAINER audit row for the outer archive.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.src.storage.models import RoutingResolution
from core.src.workflow_engine.tasks.inbound_attachment import (
    _is_archive_attachment,
    _sha256_hex,
)


class _StubAttachment:
    def __init__(self, filename, content=b"", file_hash=""):
        self.filename = filename
        self.content = content
        self.content_type = "application/octet-stream"
        self.file_hash = file_hash


class TestIsArchiveAttachment:
    def test_zip_detected(self):
        assert _is_archive_attachment(_StubAttachment("foo.zip")) is True

    def test_7z_detected(self):
        assert _is_archive_attachment(_StubAttachment("foo.7z")) is True

    def test_ooxml_not_archive(self):
        assert _is_archive_attachment(_StubAttachment("foo.xlsx")) is False
        assert _is_archive_attachment(_StubAttachment("foo.docx")) is False

    def test_regular_file_not_archive(self):
        assert _is_archive_attachment(_StubAttachment("report.pdf")) is False

    def test_empty_filename(self):
        assert _is_archive_attachment(_StubAttachment("")) is False


class TestSha256Hex:
    def test_matches_known_hash(self):
        # sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        assert _sha256_hex(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_stable_across_calls(self):
        data = b"hilda-archive-dispatch"
        assert _sha256_hex(data) == _sha256_hex(data)

    def test_differs_for_different_content(self):
        assert _sha256_hex(b"a") != _sha256_hex(b"b")


class TestArchiveContainerEnumValue:
    """The outer archive audit row writes routing_resolution=ArchiveContainer.
    Guard the value so a rename in the enum doesn't silently break the
    persisted rows or downstream ops queries filtering on this string."""

    def test_archive_container_string_value_stable(self):
        assert RoutingResolution.ARCHIVE_CONTAINER.value == "ArchiveContainer"


class TestDocTypeArchiveContainerSentinel:
    """DOCTYPE-1 (2026-08-08): outer-archive audit row must persist. Prior
    fix wrote `doc_type=""` which failed Pydantic validation and silently
    dropped the row. `DocType.ARCHIVE_CONTAINER` sentinel now satisfies
    the enum-typed `doc_type` field."""

    def test_archive_container_enum_value(self):
        from core.src.template_schema.enums import DocType
        assert DocType.ARCHIVE_CONTAINER.value == "archive_container"

    def test_document_index_row_accepts_archive_container_doc_type(self):
        # Regression: pre-fix, this constructor raised ValidationError
        # (doc_type="" is not a valid DocType member).
        from datetime import datetime, timezone
        from core.src.storage.models import DocumentIndexRow, RoutingResolution
        from core.src.template_schema.enums import DocType, IngestSource
        row = DocumentIndexRow(
            file_hash="a" * 64,
            milestone_id="ms-1",
            customer_id="MMK",
            device_id="SM-TEST",
            doc_type=DocType.ARCHIVE_CONTAINER.value,
            doc_id_slug=None,
            rev_number=None,
            ingest_source=IngestSource.EMAIL.value,
            original_filename="pack.zip",
            first_page_excerpt="",
            is_final=False,
            inferred_tg_name=None,
            routing_resolution=RoutingResolution.ARCHIVE_CONTAINER.value,
            ingested_at=datetime.now(timezone.utc),
        )
        assert row.doc_type == DocType.ARCHIVE_CONTAINER
        assert row.routing_resolution == RoutingResolution.ARCHIVE_CONTAINER.value

    def test_empty_string_doc_type_still_rejected(self):
        # Guard against a well-meaning refactor re-introducing the bug.
        import pydantic
        from datetime import datetime, timezone
        from core.src.storage.models import DocumentIndexRow, RoutingResolution
        from core.src.template_schema.enums import IngestSource
        with pytest.raises(pydantic.ValidationError):
            DocumentIndexRow(
                file_hash="a" * 64,
                milestone_id="ms-1",
                customer_id="MMK",
                device_id="SM-TEST",
                doc_type="",
                doc_id_slug=None,
                rev_number=None,
                ingest_source=IngestSource.EMAIL.value,
                original_filename="pack.zip",
                first_page_excerpt="",
                is_final=False,
                inferred_tg_name=None,
                routing_resolution=RoutingResolution.ARCHIVE_CONTAINER.value,
                ingested_at=datetime.now(timezone.utc),
            )


class TestZipRoundTripThroughExtractor:
    """Confirms the extractor path used by _process_archive_attachment
    hands back inner entries in the shape the pipeline expects. Guards
    against future extractor-side breakage that would silently zero-out
    the inner routing loop."""

    def test_flat_zip_yields_expected_entries(self):
        from core.src.storage.archive_extractor import extract_archive
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", b"aa")
            zf.writestr("sub/b.txt", b"bb")
        r = extract_archive("pack.zip", buf.getvalue())
        assert r.status == "extracted"
        parts = {e.relative_parts for e in r.entries}
        assert ("a.txt",) in parts
        assert ("sub", "b.txt") in parts
        # Inner-file synthesized filename would be "sub/b.txt" — must
        # split correctly in the view-tree writer.
        inner_filenames = ["/".join(e.relative_parts) for e in r.entries]
        assert "sub/b.txt" in inner_filenames

    def test_7z_yields_expected_entries(self):
        py7zr = pytest.importorskip("py7zr")
        from core.src.storage.archive_extractor import extract_archive
        buf = io.BytesIO()
        with py7zr.SevenZipFile(buf, "w") as z:
            z.writestr(b"aa", "a.txt")
            z.writestr(b"bb", "sub/b.txt")
        r = extract_archive("pack.7z", buf.getvalue())
        assert r.status == "extracted"
        parts = {e.relative_parts for e in r.entries}
        assert ("a.txt",) in parts
        assert ("sub", "b.txt") in parts


# ============================================================================
# NEST-1 (2026-08-03) — recursive archive extraction
# ============================================================================


def _make_zip(entries: dict[str, bytes]) -> bytes:
    """Build zip bytes from {filename: content_bytes} dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class _NestSpy:
    """Records which leaf attachments reach _process_regular_attachment
    (filename, hash) so we can assert the recursive dispatch pipeline
    handed the right leaves to routing."""
    def __init__(self):
        self.regular_calls: list[str] = []      # leaf filenames
        self.archive_calls: list[tuple[str, int]] = []  # (filename, depth)
        self.replicate_calls: list[str] = []    # outer filenames that replicated

    async def spy_regular(self, **kw):
        att = kw["attachment"]
        self.regular_calls.append(att.filename)
        return {
            "processed": 1, "routed_with_match": 0, "routed_unrouted": 1,
            "duplicates": 0, "items_incremented": set(), "events_fired": 0,
        }

    async def spy_replicate(self, **kw):
        self.replicate_calls.append(kw["filename"])


@pytest.fixture
def nest_env(monkeypatch):
    """Wire spies + storage/audit no-ops so _process_archive_attachment can
    run without a real DB / router / NSD mount."""
    spy = _NestSpy()
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.inbound_attachment._process_regular_attachment",
        spy.spy_regular,
    )
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.inbound_attachment._replicate_outer_archive_to_tgs",
        spy.spy_replicate,
    )
    async def _noop_audit(*a, **kw): pass
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.inbound_attachment._audit",
        _noop_audit,
    )
    async def _noop_save(*a, **kw): pass
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.inbound_attachment._save_outer_archive_to_default_tg",
        _noop_save,
    )
    return spy


class _NoopStorage:
    def add_document_index_row(self, row): pass


def _mk_deps():
    from types import SimpleNamespace
    return SimpleNamespace(storage=_NoopStorage())


def _mk_att(name: str, content: bytes):
    return _StubAttachment(name, content=content, file_hash=_sha256_hex(content))


class TestNestedArchiveRecursion:

    async def test_nested_zip_expands_and_prefixes_paths(self, nest_env):
        """outer.zip -> a.pdf, b/c.docx, inner.zip. inner.zip -> d.xlsx.
        Verifies:
          - a.pdf and b/c.docx routed as leaves (unchanged)
          - d.xlsx routed with prefixed filename inner.zip/d.xlsx
          - inner.zip NOT routed as a leaf (it recursed)"""
        from core.src.workflow_engine.tasks.inbound_attachment import (
            _process_archive_attachment,
        )
        inner_bytes = _make_zip({"d.xlsx": b"dd"})
        outer_bytes = _make_zip({
            "a.pdf":     b"aa",
            "b/c.docx":  b"bb",
            "inner.zip": inner_bytes,
        })
        await _process_archive_attachment(
            deps=_mk_deps(), router=None,
            attachment=_mk_att("outer.zip", outer_bytes),
            candidate_items=[], batch_id="BATCH-x", correlation_id="corr-1",
        )
        assert set(nest_env.regular_calls) == {"a.pdf", "b/c.docx", "inner.zip/d.xlsx"}
        # inner.zip must NOT appear as a leaf routing target
        assert "inner.zip" not in nest_env.regular_calls

    async def test_depth_cap_treats_deep_inner_archive_as_blob(self, nest_env):
        """Build a chain of 6 nested zips (deeper than _MAX_ARCHIVE_RECURSION_DEPTH=5).
        The depth-6 archive should fall through to _process_regular_attachment
        (opaque blob) instead of being extracted."""
        from core.src.workflow_engine.tasks.inbound_attachment import (
            _process_archive_attachment,
        )
        # Innermost has a real leaf that WOULD reach routing if extraction
        # continued.
        cur = _make_zip({"leaf.pdf": b"leaf_content"})
        # Wrap 6 times: level-6.zip contains level-5.zip contains ... level-1.zip
        for depth in range(6):
            cur = _make_zip({f"level-{depth}.zip": cur})
        await _process_archive_attachment(
            deps=_mk_deps(), router=None,
            attachment=_mk_att("outermost.zip", cur),
            candidate_items=[], batch_id="BATCH-y", correlation_id="corr-2",
        )
        # leaf.pdf should NOT have been reached (depth cap stopped the chain).
        assert "leaf.pdf" not in [c.split("/")[-1] for c in nest_env.regular_calls]
        # SOME archive along the chain must have been routed as a blob (the
        # one at depth=5 where the cap fired). Its filename ends in .zip.
        blob_routed = [c for c in nest_env.regular_calls if c.endswith(".zip")]
        assert len(blob_routed) >= 1, (
            f"expected at least one .zip routed as blob; got {nest_env.regular_calls}"
        )

    async def test_cycle_detection_self_referential_archive(self, nest_env):
        """Contrived: an archive that appears to reference itself by same
        file_hash. Bypass real extraction by injecting the same bytes at both
        levels — _seen_hashes set catches the second occurrence."""
        from core.src.workflow_engine.tasks.inbound_attachment import (
            _process_archive_attachment,
        )
        # Two-level nest where BOTH archives have identical bytes (same hash).
        # First (outer) extracts fine; when the loop enters the inner archive
        # with the SAME hash, cycle detection fires + audit + return empty.
        inner = _make_zip({"leaf.pdf": b"content-for-hash-collision-test"})
        # Wrap once — outer contains inner.zip with content == inner. When
        # the recursion enters inner.zip (same hash as outer? no — outer's
        # hash is over outer_bytes which is different from inner). To force
        # a real cycle we make outer contain a copy of ITSELF -- infeasible.
        # Instead test the mechanism by pre-seeding _seen_hashes with the
        # inner's hash before calling.
        outer_bytes = _make_zip({"inner.zip": inner})
        inner_hash = _sha256_hex(inner)
        outer_att = _mk_att("outer.zip", outer_bytes)
        seen = {inner_hash}    # pretend we've already visited inner
        await _process_archive_attachment(
            deps=_mk_deps(), router=None,
            attachment=outer_att,
            candidate_items=[], batch_id="BATCH-z", correlation_id="corr-3",
            _seen_hashes=seen,
        )
        # inner.zip should NOT recurse into leaf.pdf; instead treated as blob
        assert "inner.zip/leaf.pdf" not in nest_env.regular_calls
        assert "inner.zip" in nest_env.regular_calls   # routed as blob

    async def test_doc_count_excludes_archive_containers(self, nest_env):
        """3-file outer archive containing 1 leaf + 1 inner archive (with 2
        leaves inside). Total LEAVES = 3 (a.pdf, inner.zip/x.txt,
        inner.zip/y.txt). doc_count via stats.processed should == 3 -- the
        inner.zip container itself is NOT counted."""
        from core.src.workflow_engine.tasks.inbound_attachment import (
            _process_archive_attachment,
        )
        inner_bytes = _make_zip({"x.txt": b"xx", "y.txt": b"yy"})
        outer_bytes = _make_zip({
            "a.pdf":     b"aa",
            "inner.zip": inner_bytes,
        })
        stats = await _process_archive_attachment(
            deps=_mk_deps(), router=None,
            attachment=_mk_att("outer.zip", outer_bytes),
            candidate_items=[], batch_id="BATCH-c", correlation_id="corr-4",
        )
        assert stats["processed"] == 3   # a.pdf + x.txt + y.txt (no inner.zip)
        assert set(nest_env.regular_calls) == {
            "a.pdf", "inner.zip/x.txt", "inner.zip/y.txt",
        }

    async def test_outer_replicate_fires_once_across_nested_recursion(self, nest_env):
        """Only the OUTERMOST archive replicates to matched TGs. Inner
        archives skip replication (they'd re-materialize container bytes
        already inside the outer's payload). Verify _replicate is called
        with the outer filename only."""
        from core.src.workflow_engine.tasks.inbound_attachment import (
            _process_archive_attachment,
        )
        # Force a matched item so replication CAN fire (spy_regular returns
        # 0 items_incremented so real matched_tgs stays empty; monkey-patch
        # spy_regular to return an incremented item for one call).
        matched_items = ["MMK-SM-A-M-5"]
        candidates = [{
            "item_id": "MMK-SM-A-M-5", "customer_id": "MMK", "device_id": "SM-A",
            "milestone_id": "M", "tg_name": "TG-A", "item_type": "test_tech_waiver_report",
        }]
        call_count = {"n": 0}
        async def spy_with_match(**kw):
            call_count["n"] += 1
            return {
                "processed": 1, "routed_with_match": 1, "routed_unrouted": 0,
                "duplicates": 0,
                "items_incremented": set(matched_items),
                "events_fired": 1,
            }
        import core.src.workflow_engine.tasks.inbound_attachment as ia
        ia._process_regular_attachment = spy_with_match

        inner_bytes = _make_zip({"d.xlsx": b"dd"})
        outer_bytes = _make_zip({"inner.zip": inner_bytes})
        await _process_archive_attachment(
            deps=_mk_deps(), router=None,
            attachment=_mk_att("outer.zip", outer_bytes),
            candidate_items=candidates, batch_id="BATCH-r",
            correlation_id="corr-5",
        )
        # Exactly one replicate call, for the OUTER filename.
        assert nest_env.replicate_calls == ["outer.zip"]
