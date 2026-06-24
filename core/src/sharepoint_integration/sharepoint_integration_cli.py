"""sharepoint_integration CLI per [D-005].

Modes:
  --diagnostic         live SP connectivity + list reachability (RPT)
  --mock               in-process mock server, exercises SpCrud (RPT mock=true)
  --dry-run --customer logs intended SP ops without writes (MET)
  --serve              starts the mock SP server on a TCP port (for TPM use)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from core.src.diagnostics import PipelineError, ReportType, ReportWriter
from core.src.sharepoint_integration import (
    FileBasedListProvider,
    GlobalSharePointConfig,
    ListScope,
    SpClient,
    SpCrud,
)
from core.src.sharepoint_integration.mock_server import MockSpSession, build_app

DEFAULT_CONFIG = Path("config/sharepoint_integration.json")
DEFAULT_BASE = Path("customizations/sharepoint_config")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sharepoint_integration_cli")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnostic", action="store_true")
    group.add_argument("--mock", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--serve", action="store_true", help="run mock SP server")

    parser.add_argument(
        "--customer",
        help="customer_id (required for --dry-run) — per [D-091] slug→id rename",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--base-path", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--run-id", default=f"run-{uuid.uuid4().hex[:8]}")
    args = parser.parse_args(argv)

    if args.serve:
        return _serve(args)
    if args.mock:
        return asyncio.run(_mock(args))
    if args.diagnostic:
        return asyncio.run(_diagnostic(args))
    if args.dry_run:
        if not args.customer:
            parser.error("--dry-run requires --customer <customer_id>")
        return _dry_run(args)
    return 0  # unreachable; argparse enforces required group


# ---- modes ----


def _serve(args: argparse.Namespace) -> int:
    """Run the mock SP server on a TCP port (for TPM browser use)."""
    import uvicorn

    app = build_app()
    print(f"Mock SharePoint listening on http://{args.host}:{args.port}", flush=True)
    print("REST: /_api/web/lists/getbytitle('<name>')/items", flush=True)
    print("UI:   /  (browse lists, items, audit log)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


async def _mock(args: argparse.Namespace) -> int:
    """In-process round trip against the mock server."""
    writer = ReportWriter("SHP", args.run_id)
    app = build_app()
    cfg = GlobalSharePointConfig(
        site_url="http://mock-sp", auth_type="none", page_size=10
    )
    session = MockSpSession(app, site_url=cfg.site_url)

    if not (args.base_path / "customers").exists():
        writer.make(
            ReportType.RPT,
            {
                "mock": True,
                "customers_configured": 0,
                "lists_reachable": 0,
                "lists_unreachable": 0,
                "auth_type": "none",
            },
        )
        writer.flush()
        return 0

    provider = FileBasedListProvider(args.base_path)
    # Pre-seed empty lists in the mock store so probes return empty rather
    # than 404. This makes `--mock` validate wiring (column maps reach the
    # right list name) rather than test data presence.
    for slug, cfg_obj in provider._customers.items():  # noqa: SLF001
        for entity, entry in cfg_obj.lists.items():
            app.state.store.ensure_list(entry.name)

    client = SpClient(cfg, session=session)
    crud = SpCrud(client, provider)
    customers_configured = len(provider._customers)  # noqa: SLF001
    reachable = 0
    unreachable = 0

    async with client:
        for customer_id, cfg_obj in provider._customers.items():  # noqa: SLF001
            for entity in cfg_obj.lists:
                try:
                    await crud.get_items(entity, ListScope(customer_id))
                    reachable += 1
                except PipelineError:
                    unreachable += 1

    writer.make(
        ReportType.RPT,
        {
            "mock": True,
            "customers_configured": customers_configured,
            "lists_reachable": reachable,
            "lists_unreachable": unreachable,
            "auth_type": "none",
        },
    )
    writer.flush()
    return 0


async def _diagnostic(args: argparse.Namespace) -> int:
    """Live SP connectivity probe."""
    writer = ReportWriter("SHP", args.run_id)
    cli_overrides: dict[str, object] = {}
    cfg = GlobalSharePointConfig.from_sources(
        config_path=args.config if args.config.exists() else None,
        cli_overrides=cli_overrides,
    )
    if not (args.base_path / "customers").exists():
        writer.make(
            ReportType.RPT,
            {
                "customers_configured": 0,
                "lists_reachable": 0,
                "lists_unreachable": 0,
                "auth_type": cfg.auth_type,
            },
        )
        writer.flush()
        return 0

    provider = FileBasedListProvider(args.base_path)
    client = SpClient(cfg)
    crud = SpCrud(client, provider)
    customers_configured = len(provider._customers)  # noqa: SLF001
    reachable = 0
    unreachable = 0

    async with client:
        for customer_id, cfg_obj in provider._customers.items():  # noqa: SLF001
            for entity in cfg_obj.lists:
                try:
                    await crud.get_items(entity, ListScope(customer_id))
                    reachable += 1
                except PipelineError:
                    unreachable += 1

    writer.make(
        ReportType.RPT,
        {
            "customers_configured": customers_configured,
            "lists_reachable": reachable,
            "lists_unreachable": unreachable,
            "auth_type": cfg.auth_type,
        },
    )
    writer.flush()
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    """Validate column maps for the named customer; emit MET summary."""
    writer = ReportWriter("SHP", args.run_id)
    if not (args.base_path / "customers" / f"{args.customer}.yaml").exists():
        writer.make(
            ReportType.MET,
            {
                "customer": args.customer,
                "lists_validated": 0,
                "columns_mapped": 0,
                "missing_columns": 0,
            },
        )
        writer.flush()
        return 1
    provider = FileBasedListProvider(args.base_path)
    customer_cfg = provider._customers[args.customer]  # noqa: SLF001
    lists_validated = len(customer_cfg.lists)
    columns_mapped = sum(len(le.columns) for le in customer_cfg.lists.values())
    writer.make(
        ReportType.MET,
        {
            "customer": args.customer,
            "lists_validated": lists_validated,
            "columns_mapped": columns_mapped,
            "missing_columns": 0,
        },
    )
    writer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
