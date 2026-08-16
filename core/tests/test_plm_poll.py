"""PLM-3/5 tests -- pure-python poll_plm_once() with monkey-patched
on_prem_client + storage stubs. Router-integration seam
_ingest_new_plm_file is monkey-patched to a call-recorder (same trick
as test_nsd2.py) so we can assert per-file dispatch without dragging
real router deps in."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures / builders
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


def _seed_template_cache(customers=None):
    from core.src.template_schema import template_lookup
    template_lookup._CACHE.clear()
    if customers is None:
        customers = {"MMK": {"devices": {"SM-A015V": {}}, "milestones": {"P1": {}}}}
    for cid, template in customers.items():
        template_lookup._CACHE[cid] = template


def _make_item(**overrides):
    defaults = dict(
        delivery_item_id="MMK-SM-A015V-P1-10",
        item_id="MMK-SM-A015V-P1-10",
        item_no=10,
        item_name="Test Item 10",
        tg_name="MQL-FIT",
        device_id="SM-A015V",
        milestone_id="P1",
        customer_id="MMK",
        delivery_state="Open",
        # OWNER-7 (B-final-B): unsuffixed owner_* fields are list-typed.
        owner_corp_email=["alice@corp.example"],
        owner_corp_id=["ALICE_ID"],   # PLM assignee = [0]
        plm_id="",
        actual_item_info="",
        # PLM-7: tracking_modality gate -- defaults to opted-in for tests
        tracking_modality=["CorporatePLM"],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def mock_client(monkeypatch):
    """Monkey-patch on_prem_client's public functions at the source module.
    plm_poll imports `on_prem_client` inside each function call, so patching
    the source module's attributes wins regardless of import site."""
    client = MagicMock()
    client.create_plm_ticket.return_value = ("P20260814-99999", "https://plm.corp/detail/K12345678")
    client.list_and_download_all.return_value = 0
    monkeypatch.setattr(
        "core.src.issue_tracker.corp_plm.on_prem_client.create_plm_ticket",
        client.create_plm_ticket,
    )
    monkeypatch.setattr(
        "core.src.issue_tracker.corp_plm.on_prem_client.list_and_download_all",
        client.list_and_download_all,
    )
    return client


@pytest.fixture
def ingest_recorder(monkeypatch):
    """Monkey-patch _ingest_new_plm_file to a recorder so we don't drag
    the real router pipeline into these tests."""
    calls: list[dict] = []
    def _fake(**kwargs):
        calls.append(dict(kwargs))
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.plm_poll._ingest_new_plm_file",
        _fake,
    )
    return calls


def _stub_download_writes_files(client, files: dict[str, bytes]):
    """Wire the mock's list_and_download_all to actually create files in
    the download_dir so the walk step has something to yield."""
    def _fake_download(case_id, download_dir):
        downloads = Path(download_dir) / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            path = downloads / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return 0
    client.list_and_download_all.side_effect = _fake_download


# ---------------------------------------------------------------------------
# Sanity + no-op paths
# ---------------------------------------------------------------------------


class TestNoOpPaths:

    def test_no_deps_short_circuits(self):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        stats = poll_plm_once(SimpleNamespace(storage=None))
        assert stats["outcome"] == "no_deps"

    def test_no_p1_in_template_yields_zero_scopes(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache({
            "MMK": {"devices": {"SM-A015V": {}}, "milestones": {"DRR": {}, "PA1": {}}},
        })
        deps = SimpleNamespace(storage=_StubStorage(items=[_make_item()]), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["scopes_scanned"] == 0
        assert stats["eligible_items"] == 0
        assert mock_client.create_plm_ticket.call_count == 0

    def test_no_eligible_items_skips_group_processing(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        # Only HW PL items (not MQL-FIT / MNO-SOLUTION) -- filter drops all
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(tg_name="HW PL")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["scopes_scanned"] == 1
        assert stats["eligible_items"] == 0
        assert stats["tg_groups"] == 0


# ---------------------------------------------------------------------------
# Filter + grouping logic
# ---------------------------------------------------------------------------


class TestFilterAndGroup:

    def test_mql_fit_eligible(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(tg_name="MQL-FIT")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 1

    def test_mno_solution_eligible(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(tg_name="MNO-SOLUTION")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 1

    def test_terminal_state_dropped(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[
                _make_item(delivery_state="Closed"),
                _make_item(delivery_state="Cancelled", delivery_item_id="X-2"),
                _make_item(delivery_state="Open", delivery_item_id="X-3"),  # only this one kept
            ]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 1

    def test_tracking_modality_missing_corporate_plm_dropped(self, mock_client, ingest_recorder):
        """PLM-7: TPM opts an item into PLM polling by adding 'CorporatePLM'
        to tracking_modality. Items without it are dropped even when
        tg_name matches."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(tracking_modality=["Email"])]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 0

    def test_tracking_modality_empty_dropped(self, mock_client, ingest_recorder):
        """PLM-7 strict gate: empty tracking_modality is NOT opted in
        (per architect 2026-08-14; P1 not TPM-live yet, no backward compat)."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(tracking_modality=[])]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 0

    def test_tracking_modality_multi_value_includes_plm(self, mock_client, ingest_recorder):
        """PLM-7: multi-value modality per D-037 -- must include CorporatePLM."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(
                tracking_modality=["Email", "CorporatePLM", "NetworkSharedDrive"],
            )]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 1

    def test_wrong_device_id_dropped(self, mock_client, ingest_recorder):
        """Items whose device_id doesn't match the scope's device are dropped."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache({
            "MMK": {"devices": {"SM-A015V": {}, "SM-S671U1": {}}, "milestones": {"P1": {}}},
        })
        # Item is for SM-S671U1 but storage returns it for both scope queries.
        # Scope for SM-A015V should filter it out; scope for SM-S671U1 should keep.
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(device_id="SM-S671U1")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["scopes_scanned"] == 2
        assert stats["eligible_items"] == 1  # only picked up by matching-device scope

    def test_tg_grouping_two_tgs_two_tickets(self, mock_client, ingest_recorder):
        """OWNER-5: two TGs on the same device -> two tickets, even if the
        owner (owner_corp_id) happens to coincide. Grouping key is tg_name."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        items = [
            _make_item(delivery_item_id="X-1", item_no=1, tg_name="MQL-FIT"),
            _make_item(delivery_item_id="X-2", item_no=2, tg_name="MQL-FIT"),
            _make_item(delivery_item_id="X-3", item_no=3, tg_name="MNO-SOLUTION"),
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["tg_groups"] == 2
        assert mock_client.create_plm_ticket.call_count == 2  # one per TG

    def test_tg_grouping_same_tg_one_ticket(self, mock_client, ingest_recorder):
        """OWNER-5: N items in the SAME TG -> ONE ticket, regardless of any
        owner_corp_email variation among them (owner is per-TG per architect;
        owner_corp_email is not the grouping key anymore)."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        items = [
            _make_item(delivery_item_id="X-1", item_no=1, tg_name="MQL-FIT",
                       owner_corp_email=["alice@corp"]),
            _make_item(delivery_item_id="X-2", item_no=2, tg_name="MQL-FIT",
                       owner_corp_email=["bob@corp"]),  # ignored -- same TG
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["tg_groups"] == 1
        assert mock_client.create_plm_ticket.call_count == 1

    def test_group_missing_owner_corp_id_skipped_with_stat(self, mock_client, ingest_recorder):
        """OWNER-5/7: if no item in the TG group has a non-empty
        owner_corp_id list entry, the group is WARN + skipped; counter
        tg_groups_missing_owner_corp_id increments; no ticket is created."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        items = [
            _make_item(delivery_item_id="X-1", item_no=1, owner_corp_id=[]),
            _make_item(delivery_item_id="X-2", item_no=2, owner_corp_id=[""]),  # empty entry
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["eligible_items"] == 2
        assert stats["tg_groups"] == 1
        assert stats["tg_groups_missing_owner_corp_id"] == 1
        assert stats["tickets_created"] == 0
        mock_client.create_plm_ticket.assert_not_called()

    def test_group_assignee_from_list_first_entry(self, mock_client, ingest_recorder):
        """OWNER-5/7: assignee is first non-empty entry of owner_corp_id
        (unsuffixed field is list-typed post B-final-B). Passed to
        create_plm_ticket as the `owner_corp_id` kwarg."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[
                _make_item(owner_corp_id=["ALICE_ID", "BOB_ID"]),
            ]),
            sp_writer=None,
        )
        poll_plm_once(deps)
        _, kwargs = mock_client.create_plm_ticket.call_args
        assert kwargs["owner_corp_id"] == "ALICE_ID"

    def test_group_assignee_skips_empty_list_entries(self, mock_client, ingest_recorder):
        """OWNER-7: first non-empty entry wins; empty-string entries in
        the list are skipped defensively."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[
                _make_item(owner_corp_id=["", "  ", "REAL_ID"]),
            ]),
            sp_writer=None,
        )
        poll_plm_once(deps)
        _, kwargs = mock_client.create_plm_ticket.call_args
        assert kwargs["owner_corp_id"] == "REAL_ID"


# ---------------------------------------------------------------------------
# Ticket create / reuse
# ---------------------------------------------------------------------------


class TestTicketCreateAndReuse:

    def test_create_new_ticket_when_plm_id_empty(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(storage=_StubStorage(items=[_make_item()]), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["tickets_created"] == 1
        assert stats["tickets_reused"] == 0
        # OWNER-5: create_plm_ticket now takes owner_corp_id as a required
        # kwarg (PLM assignee derived from group's owner_corp_id_list[0]).
        mock_client.create_plm_ticket.assert_called_once_with(
            "SM-A015V", [(10, "Test Item 10")], owner_corp_id="ALICE_ID",
        )

    def test_reuse_existing_plm_id_no_create(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(plm_id="P20260812-11111")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["tickets_reused"] == 1
        assert stats["tickets_created"] == 0
        mock_client.create_plm_ticket.assert_not_called()
        # But download still fires with reused plm_id
        mock_client.list_and_download_all.assert_called_once()
        assert mock_client.list_and_download_all.call_args[0][0] == "P20260812-11111"

    def test_create_failure_skips_download(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        mock_client.create_plm_ticket.return_value = ("", "")   # failure
        deps = SimpleNamespace(storage=_StubStorage(items=[_make_item()]), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["tickets_create_failed"] == 1
        assert stats["tickets_created"] == 0
        mock_client.list_and_download_all.assert_not_called()

    def test_partial_plm_id_across_items_reused(self, mock_client, ingest_recorder):
        """If ONE item in the group has plm_id (from a prior partial-success
        write), that plm_id is reused for the whole group -- no duplicate
        create."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        items = [
            _make_item(delivery_item_id="X-1", item_no=1, plm_id=""),
            _make_item(delivery_item_id="X-2", item_no=2, plm_id="P20260812-11111"),  # already has plm_id
            _make_item(delivery_item_id="X-3", item_no=3, plm_id=""),
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["tickets_reused"] == 1
        assert stats["tickets_created"] == 0


# ---------------------------------------------------------------------------
# SP-fallback read when Postgres empty
# ---------------------------------------------------------------------------


class TestSpFallbackRead:

    def test_sp_read_used_when_postgres_empty_and_sp_has_plm_id(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        sp_writer = MagicMock()
        sp_writer.get_items.return_value = [{"_sp_id": "42", "plm_id": "P20260813-77777"}]
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(plm_id="")]),
            sp_writer=sp_writer,
        )
        stats = poll_plm_once(deps)
        # SP read found the plm_id -> reuse, don't create
        assert stats["tickets_reused"] == 1
        assert stats["tickets_created"] == 0
        assert mock_client.create_plm_ticket.call_count == 0

    def test_sp_read_empty_falls_through_to_create(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        sp_writer = MagicMock()
        sp_writer.get_items.return_value = [{"_sp_id": "42", "plm_id": ""}]
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(plm_id="")]),
            sp_writer=sp_writer,
        )
        stats = poll_plm_once(deps)
        assert stats["tickets_created"] == 1

    def test_sp_writer_unwired_falls_through_to_create(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item(plm_id="")]),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["tickets_created"] == 1


# ---------------------------------------------------------------------------
# SP writeback of plm_id + actual_item_info
# ---------------------------------------------------------------------------


class TestSpWriteback:

    def test_sp_write_called_per_item_after_create(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        sp_writer = MagicMock()
        sp_writer.get_items.return_value = [{"_sp_id": "42", "plm_id": ""}]
        items = [
            _make_item(delivery_item_id="X-1", item_no=1),
            _make_item(delivery_item_id="X-2", item_no=2),
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=sp_writer)
        stats = poll_plm_once(deps)
        # One update_item per item after create
        assert stats["sp_writes_ok"] == 2
        assert sp_writer.update_item.call_count == 2
        # Verify plm_id + actual_item_info in the write payload
        call = sp_writer.update_item.call_args_list[0]
        canonical = call.kwargs["canonical_fields"]
        assert canonical == {
            "plm_id": "P20260814-99999",
            "actual_item_info": "https://plm.corp/detail/K12345678",
        }

    def test_sp_write_no_writes_on_reuse_path(self, mock_client, ingest_recorder):
        """When plm_id already exists (reuse), no SP write happens -- the
        write is only after fresh creates."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        sp_writer = MagicMock()
        items = [_make_item(plm_id="P20260812-11111")]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=sp_writer)
        stats = poll_plm_once(deps)
        assert stats["tickets_reused"] == 1
        assert stats["sp_writes_ok"] == 0
        sp_writer.update_item.assert_not_called()


# ---------------------------------------------------------------------------
# Download + ingest
# ---------------------------------------------------------------------------


class TestDownloadAndIngest:

    def test_happy_path_ingests_downloaded_files(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_writes_files(mock_client, {
            "file_a.pdf": b"content-A",
            "subdir/file_b.pdf": b"content-B",
        })
        deps = SimpleNamespace(storage=_StubStorage(items=[_make_item()]), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["downloads_ok"] == 1
        assert stats["files_yielded"] == 2
        assert stats["files_ingested"] == 2
        assert len(ingest_recorder) == 2
        filenames = sorted(c["filename"] for c in ingest_recorder)
        assert filenames == ["file_a.pdf", "subdir/file_b.pdf"]

    def test_download_failure_skips_ingest(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        mock_client.list_and_download_all.return_value = -1
        deps = SimpleNamespace(storage=_StubStorage(items=[_make_item()]), sp_writer=None)
        stats = poll_plm_once(deps)
        assert stats["downloads_failed"] == 1
        assert stats["files_yielded"] == 0
        assert len(ingest_recorder) == 0

    def test_dedup_by_hash(self, mock_client, ingest_recorder):
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        import hashlib
        _seed_template_cache()
        _stub_download_writes_files(mock_client, {
            "already_seen.pdf": b"content-A",
            "new_one.pdf": b"content-B",
        })
        existing_hash = hashlib.sha256(b"content-A").hexdigest()
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_make_item()], existing_hashes={existing_hash}),
            sp_writer=None,
        )
        stats = poll_plm_once(deps)
        assert stats["files_yielded"] == 2
        assert stats["files_dedup_skipped"] == 1
        assert stats["files_ingested"] == 1
        assert ingest_recorder[0]["filename"] == "new_one.pdf"

    def test_ingest_receives_owner_items_as_candidates(self, mock_client, ingest_recorder):
        """Router candidates = owner's items (all of them), not just the first."""
        from core.src.workflow_engine.tasks.plm_poll import poll_plm_once
        _seed_template_cache()
        _stub_download_writes_files(mock_client, {"x.pdf": b"x"})
        items = [
            _make_item(delivery_item_id="X-1", item_no=1),
            _make_item(delivery_item_id="X-2", item_no=2),
        ]
        deps = SimpleNamespace(storage=_StubStorage(items=items), sp_writer=None)
        poll_plm_once(deps)
        assert len(ingest_recorder) == 1
        candidates = ingest_recorder[0]["items"]
        assert len(candidates) == 2
        assert {getattr(c, "delivery_item_id", None) for c in candidates} == {"X-1", "X-2"}


# ---------------------------------------------------------------------------
# PLM-6: milestone-close hook (close_plm_defects_for_milestone)
# ---------------------------------------------------------------------------


class TestClosePlmDefectsForMilestone:

    @pytest.fixture
    def mock_close(self, monkeypatch):
        """Monkey-patch on_prem_client.close_plm_defect at source module."""
        close_mock = MagicMock(return_value=0)
        monkeypatch.setattr(
            "core.src.issue_tracker.corp_plm.on_prem_client.close_plm_defect",
            close_mock,
        )
        return close_mock

    def test_no_items_returns_zeros(self, mock_close):
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        deps = SimpleNamespace(storage=_StubStorage(items=[]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 0, "plm_ids_closed": 0, "plm_ids_close_failed": 0}
        mock_close.assert_not_called()

    def test_items_without_plm_id_return_zeros(self, mock_close):
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        deps = SimpleNamespace(storage=_StubStorage(items=[
            _make_item(plm_id=""),
            _make_item(delivery_item_id="X-2", plm_id=None),
        ]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r["plm_ids_found"] == 0
        mock_close.assert_not_called()

    def test_dedup_plm_ids_across_items(self, mock_close):
        """3 items share 2 unique plm_ids -> close_plm_defect called 2x."""
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        deps = SimpleNamespace(storage=_StubStorage(items=[
            _make_item(delivery_item_id="X-1", plm_id="P111"),
            _make_item(delivery_item_id="X-2", plm_id="P111"),  # dup
            _make_item(delivery_item_id="X-3", plm_id="P222"),
        ]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 2, "plm_ids_closed": 2, "plm_ids_close_failed": 0}
        assert mock_close.call_count == 2
        called_with = {c.args[0] for c in mock_close.call_args_list}
        assert called_with == {"P111", "P222"}

    def test_device_filter_excludes_other_devices_plm_ids(self, mock_close):
        """list_items_for_milestone returns items for ALL devices in milestone;
        function must filter to the requested device_id so we only close
        tickets that belong to it."""
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        deps = SimpleNamespace(storage=_StubStorage(items=[
            _make_item(delivery_item_id="X-1", device_id="SM-A015V", plm_id="P-A"),
            _make_item(delivery_item_id="X-2", device_id="SM-S671U1", plm_id="P-B"),  # other device
        ]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r["plm_ids_found"] == 1
        mock_close.assert_called_once_with("P-A")

    def test_close_failure_counted(self, mock_close):
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        mock_close.side_effect = [0, -1]  # first succeeds, second fails
        deps = SimpleNamespace(storage=_StubStorage(items=[
            _make_item(delivery_item_id="X-1", plm_id="P111"),
            _make_item(delivery_item_id="X-2", plm_id="P222"),
        ]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 2, "plm_ids_closed": 1, "plm_ids_close_failed": 1}

    def test_close_raises_still_counted(self, mock_close):
        """A raised exception in close_plm_defect (should be rare -- client
        already suppresses subprocess errors) is caught + counted as failed."""
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        mock_close.side_effect = RuntimeError("api down")
        deps = SimpleNamespace(storage=_StubStorage(items=[
            _make_item(delivery_item_id="X-1", plm_id="P111"),
        ]))
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 1, "plm_ids_closed": 0, "plm_ids_close_failed": 1}

    def test_no_deps_short_circuits(self, mock_close):
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone
        r = close_plm_defects_for_milestone(None, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 0, "plm_ids_closed": 0, "plm_ids_close_failed": 0}
        mock_close.assert_not_called()

    def test_storage_error_returns_zeros(self, mock_close):
        from core.src.workflow_engine.tasks.plm_poll import close_plm_defects_for_milestone

        class _FailingStorage:
            def list_items_for_milestone(self, m, s): raise RuntimeError("db down")

        deps = SimpleNamespace(storage=_FailingStorage())
        r = close_plm_defects_for_milestone(deps, "MMK", "SM-A015V", "P1")
        assert r == {"plm_ids_found": 0, "plm_ids_closed": 0, "plm_ids_close_failed": 0}
        mock_close.assert_not_called()
