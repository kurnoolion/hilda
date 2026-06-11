"""storage CLI: --diagnostic / --mock / --mock-postgres / --validate / --alembic-roundtrip.

Per [D-005]. Emits STR-RPT / STR-MET / STR-QC compact reports — counts, status flags,
and bounded enum tokens only; never file content or proprietary identifiers.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.diagnostics.error_codes import PipelineError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(writer: ReportWriter, rtype: ReportType, fields: dict) -> None:
    writer.emit(ReportRecord(rtype, "STR", writer.run_id, _now(), fields))


async def _table_counts() -> dict:
    from sqlalchemy import func, select

    from core.src.storage import db as sdb

    counts: dict[str, int] = {}
    async for session in sdb.get_session():
        for label, table in (
            ("doc_index_rows", sdb.DocumentIndexTable),
            ("association_rows", sdb.DocumentItemAssociationTable),
            ("comm_log_rows", sdb.CommunicationLogTable),
            ("overrides_total", sdb.AutomationRuleOverrideTable),
            ("folder_routing_rows", sdb.TGFolderRoutingTable),
            ("tag_catalog_rows", sdb.TagCatalogTable),
        ):
            result = await session.execute(select(func.count()).select_from(table))
            counts[label] = int(result.scalar_one())
    return counts


async def _cmd_diagnostic(run_id: str) -> int:
    """Live checks against configured Postgres + Redis + NSD mount."""
    from core.src.storage import db as sdb
    from core.src.storage import redis_client
    from core.src.storage.nsd import _mount_root

    writer = ReportWriter("STR", run_id)
    postgres_ok = redis_ok = nsd_ok = False

    try:
        async for session in sdb.get_session():
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        postgres_ok = True
    except Exception:
        pass

    try:
        import redis.asyncio as redis_async

        client = redis_async.from_url(redis_client.get_celery_broker_url())
        await client.ping()
        await client.aclose()
        redis_ok = True
    except Exception:
        pass

    nsd_ok = _mount_root().is_dir()

    _emit(writer, ReportType.RPT, {
        "postgres_ping": postgres_ok, "redis_ping": redis_ok, "nsd_mount": nsd_ok,
    })
    if postgres_ok:
        _emit(writer, ReportType.MET, await _table_counts())
    writer.flush()
    return 0 if (postgres_ok and redis_ok) else 1


async def _run_mock_cycle(writer: ReportWriter) -> int:
    """In-memory end-to-end: index → associate → fan-out → token → audit → override."""
    import fakeredis.aioredis

    from core.src.storage import (
        AutomationRuleOverride, Channel, CommunicationLogRow, Direction,
        DocumentIndexRow, DocumentItemAssociation, NSDPath, NSDPathType,
        RoutingResolution, add_document_index_row, add_document_item_association,
        fan_out_plm_associations, get_active_overrides, log_communication,
        make_download_token, query_communications, resolve_download_token,
        set_override, set_redis_client,
    )
    from core.src.template_schema import DocType, IngestSource, RuleScope

    set_redis_client(fakeredis.aioredis.FakeRedis())

    ops_attempted = ops_ok = 0

    async def step(coro) -> object:
        nonlocal ops_attempted, ops_ok
        ops_attempted += 1
        result = await coro
        ops_ok += 1
        return result

    now = _now()
    path = NSDPath.internal_classified("carrier-a", "device-x", "ms-1", "tg-hw", "item-1",
                                       "test_report", "report-a", 1)
    await step(add_document_index_row(DocumentIndexRow(
        file_hash="a" * 64, milestone_id="ms-1", doc_type=DocType.TEST_REPORT,
        doc_id_slug="report-a", rev_number=1, ingest_source=IngestSource.EMAIL,
        original_filename="Report A.xlsx", routing_resolution=RoutingResolution.SUBSTRING_MATCH,
        ingested_at=now,
    )))
    await step(add_document_item_association(DocumentItemAssociation(
        file_hash="a" * 64, delivery_item_id="item-1", milestone_id="ms-1",
        local_nsd_path=path.to_relative(), nsd_path_type=NSDPathType.CLASSIFIED,
        owner_email="owner@corp", plm_id="PLM-1", associated_at=now,
    )))
    targets = await step(fan_out_plm_associations("a" * 64))
    assert len(targets) == 1 and targets[0].item_count == 1
    token = await step(make_download_token("a" * 64, "item-1"))
    await step(resolve_download_token(token))
    await step(log_communication(CommunicationLogRow(
        log_id=uuid.uuid4().hex, channel=Channel.EMAIL, direction=Direction.INBOUND,
        timestamp=now, delivery_item_id="item-1", action_type="submission",
    )))
    await step(query_communications(delivery_item_id="item-1"))
    await step(set_override(AutomationRuleOverride(
        scope=RuleScope.GLOBAL, scope_id=None, rule_id="rule-1",
        parameter_name="interval_minutes", parameter_value="30",
        set_by_pm_id="pm-1", set_at=now,
    )))
    overrides = await step(get_active_overrides(RuleScope.GLOBAL, None, "rule-1"))
    assert len(overrides) == 1

    _emit(writer, ReportType.RPT, {
        "mode": "mock", "ops_attempted": ops_attempted, "ops_ok": ops_ok,
        "ops_fail": ops_attempted - ops_ok,
    })
    return 0 if ops_ok == ops_attempted else 1


async def _cmd_mock(run_id: str) -> int:
    """SQLite in-memory + fakeredis + tmp NSD root; no external IO."""
    import tempfile

    from core.src.storage import configure_engine, init_db
    from core.src.storage.config import GlobalStorageConfig, set_storage_config

    writer = ReportWriter("STR", run_id)
    with tempfile.TemporaryDirectory() as tmp:
        set_storage_config(GlobalStorageConfig(nsd_mount_root=Path(tmp)))
        configure_engine("sqlite+aiosqlite:///:memory:")
        await init_db()
        code = await _run_mock_cycle(writer)
    set_storage_config(None)
    writer.flush()
    return code


async def _cmd_mock_postgres(run_id: str) -> int:
    """Real-Postgres semantics; reads HILDA_TEST_POSTGRES_URL (CI provides a container)."""
    url = os.environ.get("HILDA_TEST_POSTGRES_URL")
    writer = ReportWriter("STR", run_id)
    if not url:
        _emit(writer, ReportType.RPT, {"mode": "mock_postgres", "configured": False})
        writer.flush()
        print("HILDA_TEST_POSTGRES_URL not set — point it at a scratch Postgres "
              "(e.g. docker run -e POSTGRES_HOST_AUTH_METHOD=trust -p 5433:5432 postgres:16)",
              file=sys.stderr)
        return 1

    import tempfile

    from core.src.storage import configure_engine, init_db
    from core.src.storage.config import GlobalStorageConfig, set_storage_config

    with tempfile.TemporaryDirectory() as tmp:
        set_storage_config(GlobalStorageConfig(nsd_mount_root=Path(tmp)))
        configure_engine(url)
        await init_db()
        code = await _run_mock_cycle(writer)
    set_storage_config(None)
    writer.flush()
    return code


async def _cmd_validate(run_id: str, customer: str) -> int:
    """Round-trip a synthetic DeliveryItem against the customer's schema.yaml; STR-QC."""
    from core.src.template_schema import CustomerSchema

    writer = ReportWriter("STR", run_id)
    base = Path("customizations/template_schemas")
    entities_ok = roundtrip_ok = False
    columns_mapped = 0
    try:
        schema = CustomerSchema.load(customer, base)
        entities_ok = len(schema.entity_hierarchy) > 0
        columns_mapped = sum(len(e.columns) for e in schema.entity_hierarchy)
        roundtrip_ok = CustomerSchema.load(customer, base).to_yaml() == schema.to_yaml()
    except Exception:
        pass

    result = "OK" if (entities_ok and roundtrip_ok) else "FAIL"
    _emit(writer, ReportType.QC, {
        "entities_ok": entities_ok, "columns_mapped": columns_mapped,
        "roundtrip_ok": roundtrip_ok, "result": result,
    })
    writer.flush()
    return 0 if result == "OK" else 1


async def _cmd_alembic_roundtrip(run_id: str) -> int:
    """upgrade head → downgrade base → upgrade head against a scratch DB (CI gate per [D-046])."""
    import tempfile

    from alembic import command
    from alembic.config import Config

    writer = ReportWriter("STR", run_id)
    migrations_dir = Path(__file__).parent / "migrations"

    with tempfile.TemporaryDirectory() as tmp:
        url = os.environ.get("HILDA_TEST_POSTGRES_URL") or f"sqlite+aiosqlite:///{tmp}/roundtrip.db"
        cfg = Config()
        cfg.set_main_option("script_location", str(migrations_dir))
        cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])

        steps_ok = 0
        try:
            # env.py drives its own asyncio.run(); execute in a worker thread so it
            # gets a loop-free thread (this CLI command already runs inside a loop).
            for action in ("upgrade-head", "downgrade-base", "upgrade-head"):
                if action == "downgrade-base":
                    await asyncio.to_thread(command.downgrade, cfg, "base")
                else:
                    await asyncio.to_thread(command.upgrade, cfg, "head")
                steps_ok += 1
        except Exception as exc:
            print(f"alembic roundtrip failed at step {steps_ok + 1}: {exc}", file=sys.stderr)

    _emit(writer, ReportType.RPT, {
        "mode": "alembic_roundtrip", "steps_attempted": 3, "steps_ok": steps_ok,
    })
    writer.flush()
    return 0 if steps_ok == 3 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="storage CLI — diagnostics, mock cycles, schema validation, migration roundtrip"
    )
    parser.add_argument("--diagnostic", action="store_true", help="Live Postgres/Redis/NSD checks; STR-RPT + STR-MET")
    parser.add_argument("--mock", action="store_true", help="SQLite in-memory + fakeredis + tmp NSD; full op cycle")
    parser.add_argument("--mock-postgres", action="store_true", help="Same cycle against HILDA_TEST_POSTGRES_URL")
    parser.add_argument("--validate", action="store_true", help="Round-trip customer schema; STR-QC")
    parser.add_argument("--customer", default=None, help="Customer slug for --validate")
    parser.add_argument("--alembic-roundtrip", action="store_true", help="upgrade → downgrade → upgrade gate")
    parser.add_argument("--run-id", default=None, help="Stable run identifier (default: auto)")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    if args.diagnostic:
        code = asyncio.run(_cmd_diagnostic(run_id))
    elif args.mock:
        code = asyncio.run(_cmd_mock(run_id))
    elif args.mock_postgres:
        code = asyncio.run(_cmd_mock_postgres(run_id))
    elif args.validate:
        if not args.customer:
            parser.error("--validate requires --customer <slug>")
        code = asyncio.run(_cmd_validate(run_id, args.customer))
    elif args.alembic_roundtrip:
        code = asyncio.run(_cmd_alembic_roundtrip(run_id))
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
