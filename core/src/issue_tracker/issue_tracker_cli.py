"""IssueTracker CLI.

Modes:
  --diagnostic                                 -- adapter inventory RPT (legacy +
                                                  new corp_plm + customer_jira)
  --mock [--dry-run]                           -- legacy mock CRUD cycle (back-compat)
                                                  + new MockCorpPlmAdapter + MockCustomerJiraAdapter cycle
  --contract --adapter <slug>                  -- LEGACY C01-C10 contract suite
                                                  (generic IssueTracker Protocol)
  --plm-diagnostic --customer-id <id>          -- new (D2/D3 cascade 2026-06-25):
                                                  resolve per-customer corp PLM adapter +
                                                  emit _invoke_* availability RPT
  --jira-diagnostic --customer-id <id>         -- new (D2/D4 cascade 2026-06-25):
                                                  resolve per-customer JIRA adapter +
                                                  emit Ph-1 informational read-only smoke
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.diagnostics.error_codes import PipelineError
from core.src.issue_tracker.mock_adapter import MockIssueTracker
from core.src.issue_tracker.mocks import MockCorpPlmAdapter, MockCustomerJiraAdapter
from core.src.issue_tracker.protocol import IssueQuery, IssueRef, IssueStatus, IssueTracker


# ---------------------------------------------------------------------------
# Adapter loader
# ---------------------------------------------------------------------------


def load_adapter(slug: str, config: dict | None = None) -> IssueTracker:
    """Resolve an adapter by slug.

    Search order:
      1. "mock"  → MockIssueTracker() (built-in, no make_adapter needed)
      2. core.src.issue_tracker.<slug>_adapter  → make_adapter(config)
      3. customizations.issue_tracker.<slug>_adapter  → make_adapter(config)
    Raises ITR-E006 if the adapter is not found.
    """
    if slug == "mock":
        return MockIssueTracker()

    cfg = config or {}
    for module_path in (
        f"core.src.issue_tracker.{slug}_adapter",
        f"customizations.issue_tracker.{slug}_adapter",
    ):
        try:
            mod = importlib.import_module(module_path)
            return mod.make_adapter(cfg)
        except ModuleNotFoundError:
            continue
        except AttributeError:
            raise PipelineError(
                "ITR-E006",
                context={"slug": f"{slug} (make_adapter not exported from {module_path})"},
            )

    raise PipelineError("ITR-E006", context={"slug": slug})


# ---------------------------------------------------------------------------
# Contract runner (C01-C10)
# ---------------------------------------------------------------------------

_CONTRACT_PROJECT = "HILDA-CONTRACT"


async def _run_contract(adapter: IssueTracker) -> tuple[int, int, list[str]]:
    """Run all 10 contract checks. Returns (passed, failed, fail_methods)."""
    results: list[tuple[str, bool, str]] = []

    async def check(check_id: str, coro: Any) -> None:
        try:
            await coro
            results.append((check_id, True, ""))
        except Exception as exc:
            results.append((check_id, False, str(exc)))

    # C01 — round-trip: create → get → verify fields
    async def c01() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C01 contract", "C01 desc")
        issue = await adapter.get_issue(ref)
        assert issue.summary == "C01 contract", f"summary mismatch: {issue.summary!r}"
        assert issue.ref.source_system == adapter.source_system

    await check("C01", c01())

    # C02 — update: set fields → get → verify changed
    async def c02() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C02 original", "")
        await adapter.update_issue(ref, {"summary": "C02 updated"})
        issue = await adapter.get_issue(ref)
        assert issue.summary == "C02 updated", f"update not persisted: {issue.summary!r}"

    await check("C02", c02())

    # C03 — transition: valid state change accepted
    async def c03() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C03 transition", "")
        await adapter.transition_issue(ref, "start")
        issue = await adapter.get_issue(ref)
        assert issue.status == IssueStatus.IN_PROGRESS, f"unexpected status: {issue.status}"

    await check("C03", c03())

    # C04 — close idempotent: close twice → no error
    async def c04() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C04 close", "")
        await adapter.close_issue(ref, "done")
        await adapter.close_issue(ref, "done")  # must not raise
        issue = await adapter.get_issue(ref)
        assert issue.status == IssueStatus.CLOSED, f"unexpected status: {issue.status}"

    await check("C04", c04())

    # C05 — comment: add → CommentRef returned with correct issue_ref
    async def c05() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C05 comment", "")
        from core.src.issue_tracker.protocol import CommentRef
        cref = await adapter.add_comment(ref, "C05 comment body")
        assert isinstance(cref, CommentRef), f"not a CommentRef: {type(cref)}"
        assert cref.issue_ref.issue_id == ref.issue_id

    await check("C05", c05())

    # C06 — attachment: upload → AttachmentRef returned
    async def c06() -> None:
        from core.src.issue_tracker.protocol import AttachmentRef
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C06 attachment", "")
        tmp = Path(f"/tmp/hilda-c06-{uuid.uuid4().hex}.txt")
        tmp.write_text("contract test attachment")
        try:
            aref = await adapter.upload_attachment(ref, tmp)
            assert isinstance(aref, AttachmentRef), f"not an AttachmentRef: {type(aref)}"
        finally:
            tmp.unlink(missing_ok=True)

    await check("C06", c06())

    # C07 — search: query returns the created issue
    async def c07() -> None:
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C07 searchable", "")
        refs = [r async for r in await adapter.search(IssueQuery(project=_CONTRACT_PROJECT))]
        issue_ids = [r.issue_id for r in refs]
        assert ref.issue_id in issue_ids, f"{ref.issue_id} not in search results"

    await check("C07", c07())

    # C08 — changes: list_recent_changes since T₀
    async def c08() -> None:
        t0 = datetime.now(timezone.utc)
        ref = await adapter.create_issue(_CONTRACT_PROJECT, "C08 changes", "")
        changes = [c async for c in await adapter.list_recent_changes(ref, since=t0)]
        assert len(changes) >= 1, "no changes returned after create"
        assert all(c.changed_at >= t0 for c in changes), "change timestamp before T₀"

    await check("C08", c08())

    # C09 — idempotency: create twice same key → one issue
    async def c09() -> None:
        key = f"c09-{uuid.uuid4().hex}"
        r1 = await adapter.create_issue(_CONTRACT_PROJECT, "C09 dup", "", idempotency_key=key)
        r2 = await adapter.create_issue(_CONTRACT_PROJECT, "C09 dup", "", idempotency_key=key)
        assert r1.issue_id == r2.issue_id, f"idempotency failed: {r1.issue_id} != {r2.issue_id}"

    await check("C09", c09())

    # C10 — error surface: unknown ref → ITR-E002
    async def c10() -> None:
        fake = IssueRef(issue_id="NO-SUCH-99999", source_system=adapter.source_system, url="")
        try:
            await adapter.get_issue(fake)
            raise AssertionError("expected ITR-E002, got no exception")
        except PipelineError as exc:
            assert exc.code_id == "ITR-E002", f"expected ITR-E002 got {exc.code_id}"

    await check("C10", c10())

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    fail_methods = [cid for cid, ok, _ in results if not ok]
    return passed, failed, fail_methods


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


async def _cmd_diagnostic(run_id: str) -> None:
    writer = ReportWriter("ITR", run_id)
    adapters_found = 0
    adapters_valid = 0

    # Built-in adapters
    for slug in ("mock", "jira"):
        adapters_found += 1
        try:
            if slug == "mock":
                _ = MockIssueTracker()
                adapters_valid += 1
            else:
                import importlib
                mod = importlib.import_module("core.src.issue_tracker.jira_adapter")
                if hasattr(mod, "make_adapter"):
                    adapters_valid += 1
        except Exception:
            pass

    # Customizations -- look for any of three factory shapes:
    #   make_adapter         -- legacy IssueTracker Protocol (defecttrack)
    #   make_corp_plm_adapter -- new (D2/D3 cascade 2026-06-25)
    #   make_customer_jira_adapter -- new (D2/D4 cascade 2026-06-25)
    custom_dir = Path("customizations/issue_tracker")
    if custom_dir.exists():
        for f in custom_dir.glob("*_adapter.py"):
            stem = f.stem
            adapters_found += 1
            try:
                mod = importlib.import_module(f"customizations.issue_tracker.{stem}")
                if (
                    hasattr(mod, "make_adapter")
                    or hasattr(mod, "make_corp_plm_adapter")
                    or hasattr(mod, "make_customer_jira_adapter")
                ):
                    adapters_valid += 1
            except Exception:
                pass

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapters_found": adapters_found,
                "adapters_valid": adapters_valid,
                "adapters_invalid": adapters_found - adapters_valid,
            },
        )
    )
    writer.flush()


async def _cmd_mock_dry_run(run_id: str) -> None:
    writer = ReportWriter("ITR", run_id)
    adapter = MockIssueTracker()
    ops_attempted = 0
    ops_ok = 0

    ops = [
        adapter.create_issue("TEST", "Dry-run issue", "description"),
    ]
    ref = None
    for op_coro in ops:
        ops_attempted += 1
        try:
            result = await op_coro
            if ops_attempted == 1:
                ref = result
            ops_ok += 1
        except Exception:
            break

    if ref:
        for op_coro in [
            adapter.update_issue(ref, {"summary": "updated"}),
            adapter.add_comment(ref, "dry-run comment"),
            adapter.close_issue(ref, "done"),
        ]:
            ops_attempted += 1
            try:
                await op_coro
                ops_ok += 1
            except Exception:
                break

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapter": "mock",
                "ops_attempted": ops_attempted,
                "ops_ok": ops_ok,
                "ops_fail": ops_attempted - ops_ok,
            },
        )
    )
    writer.flush()


async def _cmd_plm_diagnostic(customer_id: str, run_id: str) -> None:
    """Resolve per-customer corp PLM adapter; emit `_invoke_*` method availability RPT.

    Per Module #13 cascade D2 + D3 2026-06-25. Loads the per-customer subclass
    from customizations/issue_tracker/<customer_id>_corp_plm_adapter.py via
    `make_corp_plm_adapter` factory. Does NOT make any actual API call.
    """
    writer = ReportWriter("ITR", run_id)
    methods_required = (
        "_invoke_create_plm",
        "_invoke_close_plm",
        "_invoke_get_documents_list",
        "_invoke_download_file",
        "_invoke_upload_file",
    )
    adapter_loaded = False
    methods_overridden = 0
    err: str | None = None
    try:
        module_path = f"customizations.issue_tracker.{customer_id}_corp_plm_adapter"
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError:
            # Fall back to the example scaffold for ops-debug visibility.
            mod = importlib.import_module(
                "customizations.issue_tracker.example_corp_plm_adapter"
            )
        factory = getattr(mod, "make_corp_plm_adapter", None)
        if factory is None:
            err = f"make_corp_plm_adapter not exported from {mod.__name__}"
        else:
            adapter = factory(customer_id)
            adapter_loaded = True
            # Inspect the MRO for which `_invoke_*` methods are overridden in the
            # subclass (NOT the base class default that raises NotImplementedError).
            from core.src.issue_tracker.corp_plm.adapter import CorpPlmAdapterBase
            for m in methods_required:
                base_fn = getattr(CorpPlmAdapterBase, m, None)
                sub_fn = getattr(type(adapter), m, None)
                if base_fn is not None and sub_fn is not None and sub_fn is not base_fn:
                    methods_overridden += 1
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapter": f"corp_plm/{customer_id}",
                "adapter_loaded": int(adapter_loaded),
                "methods_required": len(methods_required),
                "methods_overridden": methods_overridden,
                "err": err or "",
            },
        )
    )
    writer.flush()


async def _cmd_jira_diagnostic(customer_id: str, run_id: str) -> None:
    """Resolve per-customer JIRA adapter; emit Ph-1 informational smoke RPT.

    Per Module #13 cascade D2 + D4 + `[D-092]` 2026-06-25. Loads per-customer
    subclass; runs a trivial `search_issues` against MockCustomerJiraAdapter when
    real adapter unreachable so ops can verify the wiring without credentials.
    """
    writer = ReportWriter("ITR", run_id)
    ops_attempted = 0
    ops_ok = 0
    adapter_loaded = False
    used_mock = False
    err: str | None = None
    try:
        module_path = f"customizations.issue_tracker.{customer_id}_customer_jira_adapter"
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError:
            mod = importlib.import_module(
                "customizations.issue_tracker.example_customer_jira_adapter"
            )
        factory = getattr(mod, "make_customer_jira_adapter", None)
        if factory is None:
            err = f"make_customer_jira_adapter not exported from {mod.__name__}"
            adapter = MockCustomerJiraAdapter(customer_id=customer_id)
            used_mock = True
        else:
            try:
                adapter = factory(customer_id)
                adapter_loaded = True
            except Exception as exc:  # noqa: BLE001
                err = f"factory failed: {exc}"
                adapter = MockCustomerJiraAdapter(customer_id=customer_id)
                used_mock = True
        ops_attempted += 1
        try:
            await adapter.search_issues(jql="ORDER BY created DESC")
            ops_ok += 1
        except Exception as exc:  # noqa: BLE001
            err = err or f"search_issues failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapter": f"customer_jira/{customer_id}",
                "adapter_loaded": int(adapter_loaded),
                "used_mock_fallback": int(used_mock),
                "ops_attempted": ops_attempted,
                "ops_ok": ops_ok,
                "err": err or "",
            },
        )
    )
    writer.flush()


async def _cmd_mock_new(run_id: str) -> None:
    """Exercises MockCorpPlmAdapter + MockCustomerJiraAdapter (D2 cascade)."""
    writer = ReportWriter("ITR", run_id)
    plm = MockCorpPlmAdapter(customer_id="mock_carrier")
    jira = MockCustomerJiraAdapter(customer_id="mock_carrier")
    ops_attempted = 0
    ops_ok = 0

    # PLM cycle
    try:
        ops_attempted += 1
        plm_id = await plm.create_plm("MODEL-X", "tpm0", "Title", "owner0", "desc")
        if plm_id is not None:
            ops_ok += 1
        ops_attempted += 1
        await plm.get_documents_list(plm_id or "", "tpm0")
        ops_ok += 1
        ops_attempted += 1
        ok = await plm.close_plm(plm_id or "", "tpm0")
        if ok:
            ops_ok += 1
    except Exception:  # noqa: BLE001
        pass

    # JIRA cycle
    try:
        ops_attempted += 1
        await jira.search_issues(jql="ORDER BY created DESC")
        ops_ok += 1
    except Exception:  # noqa: BLE001
        pass

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapter": "mocks(corp_plm+customer_jira)",
                "ops_attempted": ops_attempted,
                "ops_ok": ops_ok,
                "ops_fail": ops_attempted - ops_ok,
            },
        )
    )
    writer.flush()


async def _cmd_contract(slug: str, run_id: str) -> None:
    writer = ReportWriter("ITR", run_id)
    try:
        adapter = load_adapter(slug)
    except PipelineError as exc:
        writer.emit(
            ReportRecord(
                ReportType.RPT,
                "ITR",
                run_id,
                datetime.now(timezone.utc),
                {
                    "adapter": slug,
                    "tests": 0,
                    "passed": 0,
                    "failed": 1,
                    "fail_methods": f"load:{exc.code_id}",
                },
            )
        )
        writer.flush()
        return

    passed, failed, fail_methods = await _run_contract(adapter)
    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "ITR",
            run_id,
            datetime.now(timezone.utc),
            {
                "adapter": slug,
                "tests": 10,
                "passed": passed,
                "failed": failed,
                "fail_methods": ",".join(fail_methods) if fail_methods else "",
            },
        )
    )
    writer.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IssueTracker CLI — diagnostic, mock dry-run, contract test"
    )
    parser.add_argument("--diagnostic", action="store_true", help="Emit ITR-RPT of adapter inventory")
    parser.add_argument("--mock", action="store_true", help="Run against MockIssueTracker (legacy) and the new Mock adapters")
    parser.add_argument("--dry-run", action="store_true", help="Run mock create/update/comment/close cycle")
    parser.add_argument("--contract", action="store_true", help="Run C01-C10 contract suite (legacy)")
    parser.add_argument("--adapter", default="mock", help="Adapter slug for --contract (default: mock)")
    parser.add_argument("--plm-diagnostic", action="store_true",
                        help="Resolve per-customer corp PLM adapter; emit _invoke_* availability RPT (D2/D3 cascade)")
    parser.add_argument("--jira-diagnostic", action="store_true",
                        help="Resolve per-customer JIRA adapter; emit Ph-1 informational smoke RPT (D2/D4 cascade)")
    parser.add_argument("--customer-id", default="example",
                        help="Customer ID for --plm-diagnostic / --jira-diagnostic (default: example)")
    parser.add_argument("--run-id", default=None, help="Stable run identifier (default: auto)")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    if args.diagnostic:
        asyncio.run(_cmd_diagnostic(run_id))
    elif args.mock and args.dry_run:
        asyncio.run(_cmd_mock_dry_run(run_id))
    elif args.mock:
        # New: exercises MockCorpPlmAdapter + MockCustomerJiraAdapter
        asyncio.run(_cmd_mock_new(run_id))
    elif args.contract:
        asyncio.run(_cmd_contract(args.adapter, run_id))
    elif args.plm_diagnostic:
        asyncio.run(_cmd_plm_diagnostic(args.customer_id, run_id))
    elif args.jira_diagnostic:
        asyncio.run(_cmd_jira_diagnostic(args.customer_id, run_id))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
