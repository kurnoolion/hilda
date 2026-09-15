"""MNO-MULTIASSOC-1 (2026-09-14) — tests for `mno_solution_doc_map` loader
and the end-to-end plm_poll -> _ingest_new_plm_file plumbing that hands
Fr52 the yaml's item_ids via `pre_routed_item_ids`.

Router-level bypass behavior (Branch B skipped when pre_routed_item_ids is
non-empty) is covered separately in test_attachment_router.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Loader unit tests
# ---------------------------------------------------------------------------


class _Item(SimpleNamespace):
    pass


def _mno_item(item_no: int, item_id: str) -> _Item:
    return _Item(item_no=item_no, item_id=item_id, delivery_item_id=item_id)


class TestLoader:

    def test_missing_yaml_returns_none_silent(self, tmp_path, caplog):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        result = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="P1",
        )
        assert result is None
        assert "MNO_DOC_MAP" not in caplog.text  # silent for the common case

    def test_happy_path_builds_reverse_index(self, tmp_path):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'CQ12345'\n"
            "mappings:\n"
            "  - item_no: 40\n"
            "    documents: ['Panel Spec.pdf', 'SAR Report.docx']\n"
            "  - item_no: 41\n"
            "    documents: ['Panel Spec.pdf']\n"
            "  - item_no: 42\n"
            "    documents: []\n",
            encoding="utf-8",
        )
        items = [
            _mno_item(40, "I-40"),
            _mno_item(41, "I-41"),
            _mno_item(42, "I-42"),
        ]
        m = load_mno_solution_doc_map(tmp_path, items, expected_plm_id="CQ12345")
        assert m is not None
        # Panel Spec.pdf legitimately in item 40 and 41 -> multi-association
        assert set(m.item_ids_for("Panel Spec.pdf") or []) == {"I-40", "I-41"}
        # SAR only in 40
        assert m.item_ids_for("SAR Report.docx") == ["I-40"]
        # Item 42 has empty documents -> no reverse-index entry
        assert m.item_ids_for("nonexistent.pdf") is None

    def test_case_insensitive_and_path_stripped(self, tmp_path):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'CQ12345'\n"
            "mappings:\n"
            "  - item_no: 40\n"
            "    documents: ['  Panel Spec.PDF  ']\n",
            encoding="utf-8",
        )
        m = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ12345",
        )
        assert m is not None
        # Lookup by any case + surrounding whitespace + subdir prefix
        assert m.item_ids_for("panel spec.pdf") == ["I-40"]
        assert m.item_ids_for("PANEL SPEC.pdf") == ["I-40"]
        assert m.item_ids_for("  Panel Spec.pdf  ") == ["I-40"]
        assert m.item_ids_for("downloads/Panel Spec.pdf") == ["I-40"]

    def test_extensions_are_distinct(self, tmp_path):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'CQ1'\n"
            "mappings:\n"
            "  - item_no: 40\n"
            "    documents: ['Panel Spec.pdf']\n"
            "  - item_no: 41\n"
            "    documents: ['Panel Spec.docx']\n",
            encoding="utf-8",
        )
        m = load_mno_solution_doc_map(
            tmp_path,
            items=[_mno_item(40, "I-40"), _mno_item(41, "I-41")],
            expected_plm_id="CQ1",
        )
        assert m is not None
        assert m.item_ids_for("Panel Spec.pdf") == ["I-40"]
        assert m.item_ids_for("Panel Spec.docx") == ["I-41"]

    def test_malformed_yaml_returns_none_warn(self, tmp_path, caplog):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: [unclosed\n", encoding="utf-8",
        )
        result = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ1",
        )
        assert result is None
        assert "yaml parse failed" in caplog.text

    def test_plm_id_mismatch_returns_none_warn(self, tmp_path, caplog):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'OTHER'\n"
            "mappings:\n"
            "  - item_no: 40\n"
            "    documents: ['a.pdf']\n",
            encoding="utf-8",
        )
        result = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ1",
        )
        assert result is None
        assert "plm_id mismatch" in caplog.text

    def test_item_no_not_in_postgres_items_skipped_with_warn(self, tmp_path, caplog):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'CQ1'\n"
            "mappings:\n"
            "  - item_no: 40\n"
            "    documents: ['a.pdf']\n"
            "  - item_no: 999\n"       # unknown
            "    documents: ['orphan.pdf']\n",
            encoding="utf-8",
        )
        m = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ1",
        )
        assert m is not None
        assert m.item_ids_for("a.pdf") == ["I-40"]
        assert m.item_ids_for("orphan.pdf") is None
        assert "item_no=999" in caplog.text

    def test_top_level_not_dict_returns_none(self, tmp_path):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "- just\n- a list\n", encoding="utf-8",
        )
        assert load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ1",
        ) is None

    def test_mapping_entry_non_dict_skipped(self, tmp_path, caplog):
        from core.src.template_schema.mno_solution_doc_map import (
            load_mno_solution_doc_map,
        )
        (tmp_path / "mno_solution_doc_map.yaml").write_text(
            "plm_id: 'CQ1'\n"
            "mappings:\n"
            "  - 'not-a-dict'\n"
            "  - item_no: 40\n"
            "    documents: ['a.pdf']\n",
            encoding="utf-8",
        )
        m = load_mno_solution_doc_map(
            tmp_path, items=[_mno_item(40, "I-40")], expected_plm_id="CQ1",
        )
        assert m is not None
        assert m.item_ids_for("a.pdf") == ["I-40"]
        assert "not a dict" in caplog.text


# ---------------------------------------------------------------------------
# plm_poll integration -- yaml at <work_dir>/mno_solution_doc_map.yaml gets
# read, and pre_routed_item_ids flows through to _ingest_new_plm_file.
# ---------------------------------------------------------------------------


class _StubStorage:
    def __init__(self, items=None, existing_hashes=None):
        self._items = items or []
        self._existing = set(existing_hashes or ())

    def list_items_for_milestone(self, milestone_id, states):
        return list(self._items)

    def get_document_index_row_by_hash(self, file_hash):
        if file_hash in self._existing:
            return SimpleNamespace(file_hash=file_hash)
        return None


def _seed_template_cache():
    from core.src.template_schema import template_lookup
    template_lookup._CACHE.clear()
    template_lookup._CACHE["MMK"] = {
        "devices": {"SM-A015V": {}}, "milestones": {"P1": {}},
    }


def _make_mno_item(item_no: int, item_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        delivery_item_id=item_id,
        item_id=item_id,
        item_no=item_no,
        item_name=f"MNO Item {item_no}",
        tg_name="MNO-Solution",
        device_id="SM-A015V",
        milestone_id="P1",
        customer_id="MMK",
        delivery_state="Open",
        owner_corp_email=["alice@corp.example"],
        owner_corp_id=["ALICE_ID"],
        plm_id="",
        actual_item_info="",
        tracking_modality=["CorporatePLM"],
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    client.create_plm_ticket.return_value = (
        "P20260914-42", "https://plm.corp/detail/K42",
    )
    client.list_and_download_all.return_value = 0
    client.decrypt_plm_id.return_value = True
    monkeypatch.setattr(
        "core.src.issue_tracker.corp_plm.on_prem_client.create_plm_ticket",
        client.create_plm_ticket,
    )
    monkeypatch.setattr(
        "core.src.issue_tracker.corp_plm.on_prem_client.list_and_download_all",
        client.list_and_download_all,
    )
    monkeypatch.setattr(
        "core.src.issue_tracker.corp_plm.nasca_decrypt_client.decrypt_plm_id",
        client.decrypt_plm_id,
    )
    return client


@pytest.fixture
def ingest_recorder(monkeypatch):
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(dict(kwargs))

    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.plm_poll._ingest_new_plm_file",
        _fake,
    )
    return calls


def _stub_download_and_yaml(
    client,
    downloads: dict[str, bytes],
    yaml_text: str | None,
    *,
    yaml_name: str = "mno_solution_doc_map.yaml",
):
    """Wire list_and_download_all to write files under `downloads/` AND
    optionally drop the yaml at `<cwd>` (sibling of downloads/)."""

    def _fake_download(case_id, download_dir):
        d = Path(download_dir) / "downloads"
        d.mkdir(parents=True, exist_ok=True)
        for name, content in downloads.items():
            fp = d / name
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(content)
        if yaml_text is not None:
            (Path(download_dir) / yaml_name).write_text(yaml_text, encoding="utf-8")
        return 0

    client.list_and_download_all.side_effect = _fake_download


class TestPlmPollYamlIntegration:

    def test_no_yaml_falls_through_no_pre_routed(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_and_yaml(
            mock_client, {"file_a.pdf": b"AAA"}, yaml_text=None,
        )
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_mno_item(40, "I-40")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["files_ingested"] == 1
        assert stats.get("files_mno_yaml_routed", 0) == 0
        assert ingest_recorder[0].get("pre_routed_item_ids") is None

    def test_yaml_hit_passes_pre_routed_and_bumps_counter(
        self, mock_client, ingest_recorder,
    ):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        # Actual plm_id assigned by mock is "P20260914-42".
        _stub_download_and_yaml(
            mock_client,
            {"Panel Spec.pdf": b"panelbytes"},
            yaml_text=(
                "plm_id: 'P20260914-42'\n"
                "mappings:\n"
                "  - item_no: 40\n"
                "    documents: ['Panel Spec.pdf']\n"
                "  - item_no: 41\n"
                "    documents: ['Panel Spec.pdf']\n"
            ),
        )
        items = [_make_mno_item(40, "I-40"), _make_mno_item(41, "I-41")]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["files_ingested"] == 1
        assert stats["files_mno_yaml_routed"] == 1
        assert set(ingest_recorder[0]["pre_routed_item_ids"]) == {"I-40", "I-41"}

    def test_yaml_miss_falls_through_for_that_file_only(
        self, mock_client, ingest_recorder,
    ):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_and_yaml(
            mock_client,
            {"Panel Spec.pdf": b"AA", "Unlisted.pdf": b"BB"},
            yaml_text=(
                "plm_id: 'P20260914-42'\n"
                "mappings:\n"
                "  - item_no: 40\n"
                "    documents: ['Panel Spec.pdf']\n"
            ),
        )
        items = [_make_mno_item(40, "I-40")]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["files_ingested"] == 2
        assert stats["files_mno_yaml_routed"] == 1
        by_name = {c["filename"]: c for c in ingest_recorder}
        assert by_name["Panel Spec.pdf"]["pre_routed_item_ids"] == ["I-40"]
        assert by_name["Unlisted.pdf"].get("pre_routed_item_ids") is None

    def test_dedup_bypassed_for_yaml_hit(self, mock_client, ingest_recorder):
        """MNO-MULTIASSOC-1: when the yaml has this filename, the walker's
        early dedup-skip is bypassed so Fr52's Step 0/0b runs and can add
        new associations from a later-tick yaml edit."""
        import hashlib
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_and_yaml(
            mock_client,
            {"Panel Spec.pdf": b"panelbytes"},
            yaml_text=(
                "plm_id: 'P20260914-42'\n"
                "mappings:\n"
                "  - item_no: 40\n"
                "    documents: ['Panel Spec.pdf']\n"
            ),
        )
        existing = hashlib.sha256(b"panelbytes").hexdigest()
        deps = SimpleNamespace(
            storage=_StubStorage(
                items=[_make_mno_item(40, "I-40")],
                existing_hashes={existing},
            ),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        # Walker's own dedup-skip did NOT fire; ingest was called so Fr52
        # can add the missing association via its own Step 0/0b.
        assert stats["files_dedup_skipped"] == 0
        assert stats["files_ingested"] == 1
        assert ingest_recorder[0]["pre_routed_item_ids"] == ["I-40"]

    def test_dedup_still_fires_when_not_in_yaml(
        self, mock_client, ingest_recorder,
    ):
        """Non-yaml file at a hash that already exists: walker's early dedup
        still short-circuits (no perf regression on the common path)."""
        import hashlib
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_and_yaml(
            mock_client,
            {"Unlisted.pdf": b"AA"},
            yaml_text="plm_id: 'P20260914-42'\nmappings: []\n",
        )
        existing = hashlib.sha256(b"AA").hexdigest()
        deps = SimpleNamespace(
            storage=_StubStorage(
                items=[_make_mno_item(40, "I-40")],
                existing_hashes={existing},
            ),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["files_dedup_skipped"] == 1
        assert stats["files_ingested"] == 0

    def test_yaml_plm_id_mismatch_falls_through(
        self, mock_client, ingest_recorder,
    ):
        """Yaml present but plm_id doesn't match this batch -> ignore yaml,
        fall through to normal FR-52 routing for all files."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_and_yaml(
            mock_client,
            {"Panel Spec.pdf": b"AAA"},
            yaml_text=(
                "plm_id: 'DIFFERENT-TICKET'\n"
                "mappings:\n"
                "  - item_no: 40\n"
                "    documents: ['Panel Spec.pdf']\n"
            ),
        )
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_mno_item(40, "I-40")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["files_ingested"] == 1
        assert stats.get("files_mno_yaml_routed", 0) == 0
        assert ingest_recorder[0].get("pre_routed_item_ids") is None


# ---------------------------------------------------------------------------
# Router-side unit test: Fr52 with `pre_routed_item_ids` bypasses Branch B.
# ---------------------------------------------------------------------------


class TestRouterBranchBBypass:

    async def test_pre_routed_skips_route_to_items(self, monkeypatch):
        """Fr52.route(..., pre_routed_item_ids=[...]) must NOT call
        _route_to_items; the caller's item_ids become matches with
        RoutingResolution.MNO_YAML_DIRECT."""
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter,
        )
        from core.src.email_service.protocol import InboundAttachment
        from core.src.storage.models import RoutingResolution

        class _NoopStorage:
            async def get_document_index_row_by_hash(self, h):
                return None

            async def add_document_index_row(self, row):
                pass

            async def add_document_item_association(self, assoc):
                pass

            async def find_doc_id_slugs_for_item(self, iid, dt):
                return []

            async def get_max_rev_for_slug(self, mid, slug):
                return 0

            async def item_has_association(self, h, iid):
                return False

            async def write_file(self, path, content):
                pass

            async def log_communication(self, row):
                pass

        router = Fr52AttachmentRouter(
            storage=_NoopStorage(),
            llm=None,
            tg_resolver=None,
            doc_type_filename_rules_path=Path("/nonexistent"),
        )

        called_route_to_items = {"n": 0}
        real_r2i = router._route_to_items

        async def _spy(*args, **kwargs):
            called_route_to_items["n"] += 1
            return await real_r2i(*args, **kwargs)

        monkeypatch.setattr(router, "_route_to_items", _spy)

        att = InboundAttachment(
            filename="Panel Spec.pdf",
            content=b"AAA",
            content_type="application/pdf",
            file_hash="deadbeef" * 8,
        )
        candidates = [
            {"item_id": "I-40", "tg_name": "MNO-Solution",
             "item_type": "compliance", "delivery_state": "Open"},
            {"item_id": "I-41", "tg_name": "MNO-Solution",
             "item_type": "compliance", "delivery_state": "Open"},
        ]

        result = await router.route(
            att, "batch-1", candidates,
            pre_routed_item_ids=["I-40", "I-41"],
        )

        assert called_route_to_items["n"] == 0
        assert result.routing_resolution == RoutingResolution.MNO_YAML_DIRECT
        assert {m.item_id for m in result.matches} == {"I-40", "I-41"}
        assert all(
            m.source == RoutingResolution.MNO_YAML_DIRECT for m in result.matches
        )
        assert all(m.confidence == 1.0 for m in result.matches)

    async def test_no_pre_routed_still_calls_route_to_items(self, monkeypatch):
        """Regression guard: with pre_routed_item_ids omitted / empty /
        None, Fr52 still runs Branch B (`_route_to_items`)."""
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter,
        )
        from core.src.email_service.protocol import InboundAttachment
        from core.src.storage.models import RoutingResolution

        class _NoopStorage:
            async def get_document_index_row_by_hash(self, h):
                return None

            async def add_document_index_row(self, row):
                pass

            async def add_document_item_association(self, assoc):
                pass

            async def find_doc_id_slugs_for_item(self, iid, dt):
                return []

            async def get_max_rev_for_slug(self, mid, slug):
                return 0

            async def item_has_association(self, h, iid):
                return False

            async def write_file(self, path, content):
                pass

            async def log_communication(self, row):
                pass

        router = Fr52AttachmentRouter(
            storage=_NoopStorage(),
            llm=None,
            tg_resolver=None,
            doc_type_filename_rules_path=Path("/nonexistent"),
            ph1_first_pass_substring_only=True,
        )

        called = {"n": 0}
        real = router._route_to_items

        async def _spy(*args, **kwargs):
            called["n"] += 1
            return await real(*args, **kwargs)

        monkeypatch.setattr(router, "_route_to_items", _spy)

        att = InboundAttachment(
            filename="anything.pdf",
            content=b"BBB",
            content_type="application/pdf",
            file_hash="cafef00d" * 8,
        )
        candidates = [
            {"item_id": "D-1", "tg_name": "TG-X",
             "item_type": "default", "delivery_state": "Open"},
        ]

        result = await router.route(att, "batch-2", candidates)

        assert called["n"] == 1
        assert result.routing_resolution != RoutingResolution.MNO_YAML_DIRECT
