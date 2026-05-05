"""diagnostics CLI: --diagnostic / --validate per [D-005]."""
from __future__ import annotations

import argparse
import sys
import uuid

from core.src.diagnostics import (
    ERROR_CODES,
    PREFIX_REGISTRY,
    QC_REGISTRY,
    ReportType,
    ReportWriter,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diagnostics_cli")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnostic", action="store_true", help="emit RPT record")
    group.add_argument("--validate", action="store_true", help="emit QC validation record")
    parser.add_argument("--run-id", default=f"run-{uuid.uuid4().hex[:8]}")
    args = parser.parse_args(argv)

    writer = ReportWriter(module_prefix="DGN", run_id=args.run_id)

    prefix_count = len(PREFIX_REGISTRY)
    code_count = len(ERROR_CODES)

    if args.diagnostic:
        modules = ",".join(sorted(PREFIX_REGISTRY.keys()))
        writer.make(
            ReportType.RPT,
            {
                "prefix_count": prefix_count,
                "code_count": code_count,
                "modules": modules,
            },
        )
        writer.flush()
        return 0

    duplicate_prefixes = len(set(PREFIX_REGISTRY.values())) != len(PREFIX_REGISTRY)
    orphan_codes = any(
        code.split("-", 1)[0] not in PREFIX_REGISTRY for code in ERROR_CODES
    )
    modules_with_codes = {code.split("-", 1)[0] for code in ERROR_CODES}
    modules_with_qc = {tpl.module_prefix for tpl in QC_REGISTRY.values()}
    missing_qc_templates = bool(modules_with_codes - modules_with_qc)

    if duplicate_prefixes or orphan_codes:
        result = "FAIL"
    elif missing_qc_templates:
        result = "WARN"
    else:
        result = "OK"

    writer.make(
        ReportType.QC,
        {
            "prefix_count": prefix_count,
            "code_count": code_count,
            "duplicate_prefixes": duplicate_prefixes,
            "orphan_codes": orphan_codes,
            "missing_qc_templates": missing_qc_templates,
            "result": result,
        },
    )
    writer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
