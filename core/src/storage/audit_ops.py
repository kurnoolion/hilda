"""CommunicationLog (append-only per NFR-6), FR-31 overrides, folder routing, tag catalog."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.db import (
    AutomationRuleOverrideTable,
    CommunicationLogTable,
    TGFolderRoutingTable,
    TagCatalogTable,
    session_scope,
)
from core.src.storage.models import (
    AutomationRuleOverride,
    Channel,
    CommunicationLogRow,
    Direction,
    TGFolderRoutingRow,
    TagCatalogRow,
)
from core.src.storage.redis_client import cache_delete
from core.src.template_schema import RuleScope

__all__ = [
    "clear_override",
    "deactivate_tag",
    "get_active_overrides",
    "get_folder_routing_for_tg",
    "get_tag_catalog",
    "list_active_overrides",
    "list_all_override_rule_ids",
    "list_tag_catalog_entries",
    "log_communication",
    "query_communications",
    "reactivate_tag",
    "set_folder_routing_for_tg",
    "set_override",
    "upsert_tag",
]

_QUERY_LIMIT_CAP = 1000


_session = session_scope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- CommunicationLog ------------------------------------------------------------


async def log_communication(row: CommunicationLogRow) -> None:
    """Append-only; never updates or deletes existing rows."""
    async with _session() as session:
        session.add(
            CommunicationLogTable(
                log_id=row.log_id,
                channel=row.channel.value,
                direction=row.direction.value,
                timestamp=row.timestamp,
                delivery_item_id=row.delivery_item_id,
                device_id=row.device_id,
                sender=row.sender,
                recipients=row.recipients,
                subject=row.subject,
                summary=row.summary,
                external_message_id=row.external_message_id,
                credential_id=row.credential_id,
                action_type=row.action_type,
                attachments=row.attachments,
            )
        )
        await session.commit()


async def query_communications(
    *,
    delivery_item_id: str | None = None,
    device_id: str | None = None,
    channel: Channel | None = None,
    file_hash: str | None = None,
    action_type: str | None = None,
    pm_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CommunicationLogRow]:
    """AND-composed audit query per NFR-6; timestamp DESC; limit capped at 1000."""
    limit = min(limit, _QUERY_LIMIT_CAP)
    async with _session() as session:
        stmt = select(CommunicationLogTable)
        if delivery_item_id is not None:
            stmt = stmt.where(CommunicationLogTable.delivery_item_id == delivery_item_id)
        if device_id is not None:
            stmt = stmt.where(CommunicationLogTable.device_id == device_id)
        if channel is not None:
            stmt = stmt.where(CommunicationLogTable.channel == channel.value)
        if action_type is not None:
            stmt = stmt.where(CommunicationLogTable.action_type == action_type)
        if pm_id is not None:
            stmt = stmt.where(CommunicationLogTable.credential_id == pm_id)
        if since is not None:
            stmt = stmt.where(CommunicationLogTable.timestamp >= since)
        if until is not None:
            stmt = stmt.where(CommunicationLogTable.timestamp <= until)
        stmt = stmt.order_by(CommunicationLogTable.timestamp.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = [
            CommunicationLogRow(
                log_id=r.log_id,
                channel=Channel(r.channel),
                direction=Direction(r.direction),
                timestamp=r.timestamp,
                delivery_item_id=r.delivery_item_id,
                device_id=r.device_id,
                sender=r.sender,
                recipients=r.recipients,
                subject=r.subject,
                summary=r.summary,
                external_message_id=r.external_message_id,
                credential_id=r.credential_id,
                action_type=r.action_type,
                attachments=r.attachments or [],
            )
            for r in result.scalars().all()
        ]
    if file_hash is not None:
        # attachments is a JSON list — filter client-side for dialect portability.
        rows = [
            row for row in rows
            if any(att.get("file_hash") == file_hash for att in row.attachments)
        ]
    return rows


# --- FR-31 overrides --------------------------------------------------------------


def _scope_id_key(scope_id: str | None) -> str:
    return scope_id or ""  # "" encodes Global's NULL inside the composite PK


def _override_to_model(r: AutomationRuleOverrideTable) -> AutomationRuleOverride:
    return AutomationRuleOverride(
        scope=RuleScope(r.scope),
        scope_id=r.scope_id or None,
        rule_id=r.rule_id,
        parameter_name=r.parameter_name,
        parameter_value=r.parameter_value,
        set_by_pm_id=r.set_by_pm_id,
        set_at=r.set_at,
        expires_at=r.expires_at,
    )


def _active_filter(stmt):
    now = _utcnow()
    return stmt.where(
        (AutomationRuleOverrideTable.expires_at.is_(None))
        | (AutomationRuleOverrideTable.expires_at > now)
    )


async def get_active_overrides(
    scope: RuleScope, scope_id: str | None, rule_id: str
) -> list[AutomationRuleOverride]:
    async with _session() as session:
        stmt = select(AutomationRuleOverrideTable).where(
            AutomationRuleOverrideTable.scope == scope.value,
            AutomationRuleOverrideTable.scope_id == _scope_id_key(scope_id),
            AutomationRuleOverrideTable.rule_id == rule_id,
        )
        result = await session.execute(_active_filter(stmt))
        return [_override_to_model(r) for r in result.scalars().all()]


async def list_active_overrides(
    *, scope: RuleScope | None = None, scope_id: str | None = None
) -> list[AutomationRuleOverride]:
    async with _session() as session:
        stmt = select(AutomationRuleOverrideTable)
        if scope is not None:
            stmt = stmt.where(AutomationRuleOverrideTable.scope == scope.value)
        if scope_id is not None:
            stmt = stmt.where(AutomationRuleOverrideTable.scope_id == _scope_id_key(scope_id))
        result = await session.execute(_active_filter(stmt))
        return [_override_to_model(r) for r in result.scalars().all()]


async def set_override(override: AutomationRuleOverride) -> None:
    """Insert or replace + audit row (action_type='set_override') + cache eviction."""
    key = (
        override.scope.value,
        _scope_id_key(override.scope_id),
        override.rule_id,
        override.parameter_name,
    )
    async with _session() as session:
        row = await session.get(AutomationRuleOverrideTable, key)
        if row is None:
            row = AutomationRuleOverrideTable(
                scope=key[0], scope_id=key[1], rule_id=key[2], parameter_name=key[3],
                parameter_value=override.parameter_value,
                set_by_pm_id=override.set_by_pm_id,
                set_at=override.set_at, expires_at=override.expires_at,
            )
            session.add(row)
        else:
            row.parameter_value = override.parameter_value
            row.set_by_pm_id = override.set_by_pm_id
            row.set_at = override.set_at
            row.expires_at = override.expires_at
        await session.commit()
    await log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex, channel=Channel.SHAREPOINT, direction=Direction.INBOUND,
            timestamp=_utcnow(), credential_id=override.set_by_pm_id,
            action_type="set_override",
            summary=f"{override.scope.value}/{override.scope_id or 'global'} {override.rule_id}.{override.parameter_name}",
        )
    )
    await cache_delete(f"override:{key[0]}:{key[1]}")


async def clear_override(
    scope: RuleScope, scope_id: str | None, rule_id: str, parameter_name: str,
    *, pm_id: str,
) -> None:
    """Removes the override identified by (scope, scope_id, rule_id, parameter_name).
    pm_id is REQUIRED keyword-only — the audit row sets credential_id=pm_id per FR-31
    accountability (set_override sources it from override.set_by_pm_id; clear has no
    override object). Idempotent: clearing a non-existent override still emits the
    audit row (records the intent) but does no DB delete."""
    async with _session() as session:
        await session.execute(
            delete(AutomationRuleOverrideTable).where(
                AutomationRuleOverrideTable.scope == scope.value,
                AutomationRuleOverrideTable.scope_id == _scope_id_key(scope_id),
                AutomationRuleOverrideTable.rule_id == rule_id,
                AutomationRuleOverrideTable.parameter_name == parameter_name,
            )
        )
        await session.commit()
    await log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex, channel=Channel.SHAREPOINT, direction=Direction.INBOUND,
            timestamp=_utcnow(), credential_id=pm_id, action_type="clear_override",
            summary=f"{scope.value}/{scope_id or 'global'} {rule_id}.{parameter_name}",
        )
    )
    await cache_delete(f"override:{scope.value}:{_scope_id_key(scope_id)}")


async def list_all_override_rule_ids() -> set[str]:
    """DISTINCT rule_ids across active overrides — rule_engine's STR-W004 orphan audit."""
    async with _session() as session:
        result = await session.execute(_active_filter(select(AutomationRuleOverrideTable.rule_id).distinct()))
        return {r for (r,) in result.all()}


# --- FR-77 folder routing -----------------------------------------------------------


async def get_folder_routing_for_tg(milestone_id: str, tg_name: str) -> list[TGFolderRoutingRow]:
    async with _session() as session:
        result = await session.execute(
            select(TGFolderRoutingTable)
            .where(
                TGFolderRoutingTable.milestone_id == milestone_id,
                TGFolderRoutingTable.tg_name == tg_name,
            )
            .order_by(TGFolderRoutingTable.ingress_folder)
        )
        return [
            TGFolderRoutingRow(
                milestone_id=r.milestone_id, tg_name=r.tg_name,
                ingress_folder=r.ingress_folder, item_no=r.item_no,
                routing_notes=r.routing_notes,
            )
            for r in result.scalars().all()
        ]


async def set_folder_routing_for_tg(
    milestone_id: str, tg_name: str, entries: list[TGFolderRoutingRow],
    *, valid_item_nos: set[int],
) -> None:
    """Replace-all semantics; validates every item_no against caller-supplied
    valid_item_nos (STR-E006 on unknown) — the CALLER resolves the milestone's
    item_no set from SharePoint (architect decision 2026-06-11: storage holds no
    DeliveryItem mirror). Evicts the routing cache for the (milestone, tg) key."""
    async with _session() as session:
        for entry in entries:
            if entry.item_no not in valid_item_nos:
                raise PipelineError(
                    "STR-E006",
                    context={"item_no": entry.item_no, "milestone_id": milestone_id},
                )
        await session.execute(
            delete(TGFolderRoutingTable).where(
                TGFolderRoutingTable.milestone_id == milestone_id,
                TGFolderRoutingTable.tg_name == tg_name,
            )
        )
        for entry in entries:
            session.add(
                TGFolderRoutingTable(
                    milestone_id=milestone_id, tg_name=tg_name,
                    ingress_folder=entry.ingress_folder, item_no=entry.item_no,
                    routing_notes=entry.routing_notes,
                )
            )
        await session.commit()
    await cache_delete(f"folder_routing:{milestone_id}:{tg_name}")


# --- FR-82 tag catalog ---------------------------------------------------------------


async def get_tag_catalog(customer_id: str) -> set[str]:
    """ACTIVE tag strings only — validation hot path."""
    async with _session() as session:
        result = await session.execute(
            select(TagCatalogTable.tag).where(
                TagCatalogTable.customer_id == customer_id,
                TagCatalogTable.active.is_(True),
            )
        )
        return {t for (t,) in result.all()}


async def list_tag_catalog_entries(
    customer_id: str, include_inactive: bool = False
) -> list[TagCatalogRow]:
    async with _session() as session:
        stmt = select(TagCatalogTable).where(TagCatalogTable.customer_id == customer_id)
        if not include_inactive:
            stmt = stmt.where(TagCatalogTable.active.is_(True))
        result = await session.execute(stmt.order_by(TagCatalogTable.tag))
        return [
            TagCatalogRow(
                customer_id=r.customer_id, tag=r.tag, description=r.description,
                color=r.color, active=r.active,
            )
            for r in result.scalars().all()
        ]


async def upsert_tag(row: TagCatalogRow) -> None:
    async with _session() as session:
        existing = await session.get(TagCatalogTable, (row.customer_id, row.tag))
        if existing is None:
            session.add(
                TagCatalogTable(
                    customer_id=row.customer_id, tag=row.tag,
                    description=row.description, color=row.color, active=row.active,
                )
            )
        else:
            existing.description = row.description
            existing.color = row.color
            existing.active = row.active
        await session.commit()
    await cache_delete(f"tag_catalog:{row.customer_id}")


async def _set_tag_active(customer_id: str, tag: str, active: bool) -> None:
    async with _session() as session:
        existing = await session.get(TagCatalogTable, (customer_id, tag))
        if existing is not None:
            existing.active = active
            await session.commit()
    await cache_delete(f"tag_catalog:{customer_id}")


async def deactivate_tag(customer_id: str, tag: str) -> None:
    """Soft-deactivate — preserves historical item_description references."""
    await _set_tag_active(customer_id, tag, False)


async def reactivate_tag(customer_id: str, tag: str) -> None:
    """No-op when the row doesn't exist (caller should upsert in that case)."""
    await _set_tag_active(customer_id, tag, True)
