"""customer_adapter CLI: --diagnostic / --mock / --invoke.

Per [D-005] independent test interface + [D-002] compact reports (CAD-RPT / MET).
NFR-2: emits only bounded enum tokens + sizes + counts; never carrier UI text,
file content, or credential material.

Modes:
- --diagnostic: validates pyotp import + per-customer subclass discovery in
  customizations_dir + optional NTP skew probe. Emits CAD-RPT.
- --mock: spins up MockCustomerAdapter with one canned success result; emits
  CAD-RPT after a roundtrip. Useful for integration tests / SP UI smoke.
- --invoke --customer <id> --file <fixture> --target-dir <dir> --device-id <id>
  --milestone <name>: would perform one real upload. Ph-1 STUB -- the actual
  per-customer subclass body lives on Work PC per [D-027].
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter

from .config import CustomerAdapterConfig
from .mock_customer_adapter import MockCustomerAdapter
from .protocol import CarrierUploadResult
from .totp import ntp_skew_seconds

_PREFIX = "CAD"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(writer: ReportWriter, rtype: ReportType, fields: dict) -> None:
    writer.emit(ReportRecord(rtype, _PREFIX, writer.run_id, _now(), fields))


def _count_customer_subclasses(customizations_dir: Path) -> int:
    """Count discovered <customer_id>_adapter.py files (no import)."""
    if not customizations_dir.is_dir():
        return 0
    return sum(
        1 for p in customizations_dir.glob("*_adapter.py")
        if p.is_file() and not p.name.startswith("_")
    )


def _cmd_diagnostic(run_id: str, cfg: CustomerAdapterConfig) -> int:
    """Validate pyotp + per-customer subclass discovery + NTP probe.
    Emits CAD-RPT. Exit 0 when pyotp imports + at least the customizations_dir
    is accessible (subclass count may legitimately be 0 at fresh-deploy time).
    """
    writer = ReportWriter(_PREFIX, run_id)

    pyotp_ok = False
    try:
        import pyotp as _pyotp  # noqa: F401

        pyotp_ok = True
    except ImportError:
        pyotp_ok = False

    subclass_count = _count_customer_subclasses(cfg.customizations_dir)

    ntp_skew: float | None = None
    if cfg.diagnostic_ntp_check:
        ntp_skew = ntp_skew_seconds()

    skew_warning = (
        ntp_skew is not None and ntp_skew > cfg.ntp_skew_warn_s
    )

    _emit(writer, ReportType.RPT, {
        "pyotp_available":        pyotp_ok,
        "customizations_dir":     str(cfg.customizations_dir),
        "customer_subclasses":    subclass_count,
        "diagnostic_ntp_check":   cfg.diagnostic_ntp_check,
        "ntp_skew_s":             "unknown" if ntp_skew is None else round(ntp_skew, 2),
        "ntp_skew_warn_s":        cfg.ntp_skew_warn_s,
        "ntp_skew_warning":       skew_warning,
        "binding_strategy":       "thin_wrapper",   # [D-116]
    })
    writer.flush()
    return 0 if pyotp_ok else 1


async def _run_mock(run_id: str) -> int:
    """Spin up MockCustomerAdapter + run one canned roundtrip. Emits CAD-RPT."""
    writer = ReportWriter(_PREFIX, run_id)
    adapter = MockCustomerAdapter()
    now = _now()
    adapter.register_upload_result(
        device_id="MODEL-A",
        milestone_name="P1",
        target_dir="TestReports/Power",
        filename="abc.report",
        result=CarrierUploadResult(
            success=True,
            uploaded_filename="abc.report",
            device_id="MODEL-A",
            milestone_name="P1",
            target_dir="TestReports/Power",
            upload_started_at=now,
            upload_completed_at=now,
        ),
    )
    result = await adapter.upload_attachment(
        device_id="MODEL-A",
        milestone_name="P1",
        source_dir=Path("/tmp"),
        target_dir="TestReports/Power",
        filename="abc.report",
    )
    _emit(writer, ReportType.RPT, {
        "mode":           "mock",
        "calls_total":    len(adapter.calls),
        "success":        result.success,
        "filename":       result.uploaded_filename,
        "device_id":      result.device_id,
        "milestone_name": result.milestone_name,
    })
    writer.flush()
    return 0


def _cmd_invoke_stub(run_id: str, customer_id: str) -> int:
    """Ph-1 stub for --invoke. Real per-customer subclass body lives on Work PC."""
    writer = ReportWriter(_PREFIX, run_id)
    _emit(writer, ReportType.RPT, {
        "mode":           "invoke_stub",
        "customer_id":    customer_id,
        "stub":           True,
        "reason":         "per_customer_subclass_on_work_pc",   # [D-027]
    })
    writer.flush()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="customer_adapter CLI -- diagnostics + mock + invoke (Ph-1 stub)"
    )
    parser.add_argument("--diagnostic", action="store_true",
                        help="Validate pyotp + subclass discovery + NTP probe; CAD-RPT")
    parser.add_argument("--mock", action="store_true",
                        help="Run MockCustomerAdapter with one canned result; CAD-RPT")
    parser.add_argument("--invoke", action="store_true",
                        help="Real upload (Ph-1 stub; subclass body lives on Work PC)")
    parser.add_argument("--customer", default=None,
                        help="customer_id for --invoke mode")
    parser.add_argument("--file", default=None,
                        help="local file path for --invoke mode (Ph-1 stub)")
    parser.add_argument("--target-dir", default=None,
                        help="target Drive subdirectory for --invoke mode (Ph-1 stub)")
    parser.add_argument("--device-id", default=None,
                        help="Model_No for --invoke mode (Ph-1 stub)")
    parser.add_argument("--milestone", default=None,
                        help="milestone_name for --invoke mode (Ph-1 stub)")
    parser.add_argument("--run-id", default=None, help="Stable run identifier")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    cfg = CustomerAdapterConfig()

    if args.diagnostic:
        code = _cmd_diagnostic(run_id, cfg)
    elif args.mock:
        code = asyncio.run(_run_mock(run_id))
    elif args.invoke:
        if not args.customer:
            parser.error("--invoke requires --customer <id>")
        code = _cmd_invoke_stub(run_id, args.customer)
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
