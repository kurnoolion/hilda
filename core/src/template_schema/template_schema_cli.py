"""template_schema CLI: --diagnostic / --validate per [D-005]."""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from core.src.diagnostics import PipelineError, ReportType, ReportWriter
from core.src.template_schema import CustomerSchema

DEFAULT_BASE = Path("customizations/template_schemas")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="template_schema_cli")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnostic", action="store_true")
    group.add_argument("--validate", action="store_true")
    parser.add_argument("--customer", help="customer slug for --validate")
    parser.add_argument("--base-path", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--run-id", default=f"run-{uuid.uuid4().hex[:8]}")
    args = parser.parse_args(argv)

    writer = ReportWriter("TSC", args.run_id)

    if args.diagnostic:
        valid = 0
        invalid = 0
        if args.base_path.exists():
            for child in sorted(args.base_path.iterdir()):
                if not child.is_dir():
                    continue
                try:
                    CustomerSchema.load(child.name, args.base_path)
                    valid += 1
                except PipelineError:
                    invalid += 1
        writer.make(
            ReportType.RPT,
            {
                "customers_found": valid + invalid,
                "schemas_valid": valid,
                "schemas_invalid": invalid,
            },
        )
        writer.flush()
        return 0

    if not args.customer:
        parser.error("--validate requires --customer")
    try:
        schema = CustomerSchema.load(args.customer, args.base_path)
    except PipelineError as e:
        writer.make(
            ReportType.QC,
            {
                "customer": args.customer,
                "entity_count": 0,
                "columns_mapped": 0,
                "required_fields_covered": False,
                "sp_mappings_present": False,
                "result": "FAIL",
            },
        )
        writer.flush()
        sys.stderr.write(f"{e}\n")
        return 1

    entity_count = len(schema.entity_hierarchy)
    columns_mapped = sum(len(e.columns) for e in schema.entity_hierarchy)
    required_fields_covered = all(
        any(c.required for c in e.columns) for e in schema.entity_hierarchy
    )
    sp_mappings_present = bool(schema.sp_list_mappings)
    result = "OK" if required_fields_covered else "WARN"

    writer.make(
        ReportType.QC,
        {
            "customer": schema.customer_slug,
            "entity_count": entity_count,
            "columns_mapped": columns_mapped,
            "required_fields_covered": required_fields_covered,
            "sp_mappings_present": sp_mappings_present,
            "result": result,
        },
    )
    writer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
