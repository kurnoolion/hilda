"""process_inbound_attachments task -- Step 5.5 cascade per architect 2026-06-29.

Routes attachments from OWNER_REPLY emails through Fr52AttachmentRouter +
writes bytes to NSD + persists DocumentIndexRow + DocumentItemAssociation(s) +
increments doc_count_received per matched item + fires AttachmentReceived
TriggerEvent per matched item so downstream rules (advance_to_document_received_with_parser,
reconcile_owner_intent_on_doc_count_reached) can advance state.

Architect direction 2026-06-29 design pass:
- Ph-1 first pass: Fr52AttachmentRouter constructed with
  ph1_first_pass_substring_only=True -> Branch B Step B1 (item_description
  substring) only; B2-B5 skipped. Step C (new-vs-revision) skipped.
- Branch A doc_type classification (filename regex) runs as-is.
- Step D storage-matrix path selection runs as-is.
- Steps E-H (this task body): write file bytes + index row + association +
  doc_count increment + AttachmentReceived event.

Wiring:
- email_polling.py classifier branch enqueues this task alongside
  apply_owner_reply_task for OWNER_REPLY messages with len(attachments)>0.
- Both tasks run in parallel (Celery; no chain dependency).
- Race: owner reply Closed may guard-deny first because doc_count_not_reached;
  apply_owner_reply persists owner_intent_closed_at; this task increments
  doc_count_received + fires AttachmentReceived; reconcile_owner_intent_on_doc_count_reached
  rule catches the intent + advances state.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["process_inbound_attachments_task"]

_log = logging.getLogger(__name__)


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.inbound_attachment.process_inbound_attachments",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_inbound_attachments_task(
    self, msg_payload: dict[str, Any],
) -> dict[str, Any]:
    """Route email attachments + persist to NSD + storage + fire AttachmentReceived
    events per matched item. Pattern A counterpart to apply_owner_reply_task --
    both enqueued in parallel from email_polling for OWNER_REPLY messages.
    """
    try:
        return asyncio.run(_async_process_attachments(msg_payload))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments failed: %s: %s",
            type(exc).__name__, str(exc)[:200],
        )
        try:
            raise self.retry(exc=exc)
        except Exception:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def _async_process_attachments(msg_payload: dict[str, Any]) -> dict[str, Any]:
    from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
    from core.src.email_service.inbound.classifier import BATCH_ID_RE
    from core.src.email_service.protocol import InboundAttachment
    from core.src.workflow_engine.tasks.owner_reply import _lookup_batch_items

    subject = msg_payload.get("subject", "") or ""
    attachments_raw = msg_payload.get("attachments") or ()

    # Quick exits
    if not attachments_raw:
        return {"outcome": "no_attachments", "attachments_processed": 0}

    m = BATCH_ID_RE.search(subject)
    if m is None:
        _log.warning(
            "process_inbound_attachments: no BATCH-id in subject; skipping. "
            "subject=%r", subject[:80],
        )
        return {"outcome": "missing_batch_id", "attachments_processed": 0,
                "subject": subject[:80]}
    batch_id = m.group(0)

    correlation_id = str(uuid.uuid4())
    deps = get_task_deps()

    # Candidate items = batch scope per architect Q2 lock 2026-06-29.
    # _lookup_batch_items returns delivery_item_id + owner identity; we widen
    # each to the dict shape Fr52AttachmentRouter expects.
    batch_items = await _lookup_batch_items(batch_id)
    if not batch_items:
        await _audit(deps, "process_inbound_attachments_batch_not_found",
                     None, {
                         "batch_id": batch_id, "subject": subject[:120],
                         "correlation_id": correlation_id,
                     })
        return {"outcome": "batch_not_found", "attachments_processed": 0}

    candidate_items = _widen_candidates_for_router(deps, batch_items)

    # All items in a batch share customer_id by construction (batch is owner-
    # scoped, and owners are TG-scoped within one customer). Pick first.
    customer_id = (
        (candidate_items[0].get("customer_id") if candidate_items else None)
        or ""
    )

    # Construct router in Ph-1 first-pass mode.
    router = _build_ph1_router(deps, customer_id=customer_id)
    if router is None:
        await _audit(deps, "process_inbound_attachments_router_unavailable",
                     None, {
                         "batch_id": batch_id,
                         "correlation_id": correlation_id,
                     })
        return {"outcome": "router_unavailable", "attachments_processed": 0}

    processed = 0
    routed_with_match = 0
    routed_unrouted = 0
    duplicates = 0
    failed = 0
    items_incremented: set[str] = set()
    events_fired = 0

    for a in attachments_raw:
        try:
            attachment = InboundAttachment(**a) if isinstance(a, dict) else a
            routed = await router.route(attachment, batch_id, candidate_items)
            processed += 1
            if routed.is_duplicate:
                duplicates += 1
                await _audit(deps, "attachment_duplicate", None, {
                    "batch_id": batch_id, "file_hash": routed.file_hash,
                    "filename": getattr(attachment, "filename", "")[:120],
                    "correlation_id": correlation_id,
                })
                continue

            # Steps E + F + G + H (per design pass)
            counts = await _persist_routed_attachment(
                deps=deps,
                attachment=attachment,
                routed=routed,
                candidate_items=candidate_items,
                batch_id=batch_id,
                correlation_id=correlation_id,
            )
            if counts["match_count"] > 0:
                routed_with_match += 1
            else:
                routed_unrouted += 1
            items_incremented.update(counts["item_ids"])
            events_fired += counts["events_fired"]
        except Exception as exc:  # noqa: BLE001
            failed += 1
            _log.warning(
                "process_inbound_attachments: per-attachment failure: %s: %s",
                type(exc).__name__, str(exc)[:120],
            )
            await _audit(deps, "attachment_processing_failed", None, {
                "batch_id": batch_id,
                "filename": (a.get("filename") if isinstance(a, dict) else "")[:120],
                "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                "correlation_id": correlation_id,
            })

    _log.info(
        "process_inbound_attachments: batch=%s processed=%d "
        "routed_with_match=%d routed_unrouted=%d duplicates=%d failed=%d "
        "items_incremented=%d events_fired=%d",
        batch_id, processed, routed_with_match, routed_unrouted,
        duplicates, failed, len(items_incremented), events_fired,
    )
    return {
        "outcome":              "processed",
        "batch_id":             batch_id,
        "attachments_processed": processed,
        "routed_with_match":    routed_with_match,
        "routed_unrouted":      routed_unrouted,
        "duplicates":           duplicates,
        "failed":               failed,
        "items_incremented":    sorted(items_incremented),
        "events_fired":         events_fired,
    }


def _widen_candidates_for_router(deps, batch_items: list[dict]) -> list[dict]:
    """Fr52AttachmentRouter expects candidate_items with item_id +
    item_description + item_name + item_type + tg_name. owner_reply's
    _lookup_batch_items returns a narrower shape; here we widen by
    fetching the full DeliveryItemBase per id.

    Architect 2026-06-29 backup plan: if local row has NULL tg_path_id or
    item_path_id, JIT-fetch from SP at attachment-processing time and
    persist back. This catches items imported BEFORE the kickoff back-fill
    (commit 02951ef) landed -- those rows would still be NULL on attachment
    arrival without this safety net.
    """
    widened: list[dict] = []
    items_by_id: dict[str, Any] = {}
    for it in batch_items:
        delivery_item_id = it.get("delivery_item_id")
        if not delivery_item_id:
            continue
        try:
            item = deps.storage.get_delivery_item(delivery_item_id)
        except Exception:  # noqa: BLE001
            item = None
        if item is None:
            continue
        items_by_id[delivery_item_id] = item

    # JIT back-fill: if any item is missing tg_path_id or item_path_id, do
    # ONE SP batch read for the whole milestone+device and patch local rows.
    # Best-effort -- on SP failure we fall back to tg_name + item_no
    # synthesized paths so the cascade keeps moving (the fallback was the
    # previous behaviour; preserving it for robustness).
    missing = [
        (item_id, item) for item_id, item in items_by_id.items()
        if not getattr(item, "tg_path_id", None) or not getattr(item, "item_path_id", None)
    ]
    if missing and deps.sp_writer is not None:
        try:
            from core.src.sharepoint_integration.config import ListScope
            # All items in one batch share customer_id + milestone_id +
            # device_id (architect-confirmed batch scoping).
            sample = missing[0][1]
            customer_id = getattr(sample, "customer_id", None)
            milestone_id = getattr(sample, "milestone_id", None)
            device_id = getattr(sample, "device_id", None)
            if customer_id and milestone_id and device_id:
                rows = deps.sp_writer.get_items(
                    entity="delivery_items",
                    scope=ListScope(customer_id=customer_id),
                    canonical_filters={
                        "milestone_id":  milestone_id,
                        "project_model": device_id,
                    },
                )
                sp_by_item_no = {r.get("item_no"): r for r in rows if r.get("item_no") is not None}
                backfilled = 0
                for item_id, item in missing:
                    sp_row = sp_by_item_no.get(getattr(item, "item_no", None))
                    if not sp_row:
                        continue
                    updates: dict[str, Any] = {}
                    sp_tg_path_id = sp_row.get("tg_path_id")
                    sp_item_path_id = sp_row.get("item_path_id")
                    if sp_tg_path_id and not getattr(item, "tg_path_id", None):
                        updates["tg_path_id"] = sp_tg_path_id
                    if sp_item_path_id and not getattr(item, "item_path_id", None):
                        updates["item_path_id"] = sp_item_path_id
                    if updates:
                        try:
                            deps.storage.update_delivery_item(item_id, updates)
                            # Refresh in-memory model so the widening below
                            # picks up the new values immediately.
                            for k, v in updates.items():
                                setattr(item, k, v)
                            backfilled += 1
                        except Exception as exc:  # noqa: BLE001
                            _log.warning(
                                "JIT path-fields back-fill failed for item=%s: %s",
                                item_id, type(exc).__name__,
                            )
                if backfilled:
                    _log.info(
                        "process_inbound_attachments: JIT-backfilled tg_path_id/"
                        "item_path_id for %d/%d items missing them",
                        backfilled, len(missing),
                    )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "process_inbound_attachments: JIT path-fields SP fetch failed: %s",
                type(exc).__name__,
            )

    for delivery_item_id, item in items_by_id.items():
        widened.append({
            "item_id":               delivery_item_id,
            "item_no":               getattr(item, "item_no", None),
            "item_name":             getattr(item, "item_name", "") or "",
            "item_description":      getattr(item, "item_description", None),
            "item_type":             getattr(item, "item_type", ""),
            "tg_name":               getattr(item, "tg_name", None),
            "tg_path_id":            getattr(item, "tg_path_id", None) or getattr(item, "tg_name", None) or "_unknown_tg",
            "item_path_id":          getattr(item, "item_path_id", None) or f"item_{getattr(item, 'item_no', 'x')}",
            "owner_corp_id":         getattr(item, "owner_corp_id", "") or "",
            "owner_corp_usa_email":  getattr(item, "owner_corp_usa_email", None),
            "owner_corp_email":      getattr(item, "owner_corp_email", None),
            "owner_name":            getattr(item, "owner_name", None),
            "milestone_id":          getattr(item, "milestone_id", None),
            "customer_id":           getattr(item, "customer_id", None),
            "device_id":             getattr(item, "device_id", None),
            "folder_routing_enabled": getattr(item, "folder_routing_enabled", False),
        })
    return widened


class _AsyncStorageShim:
    """Async wrapper for the 2 storage read paths Fr52AttachmentRouter awaits.

    Fr52AttachmentRouter was designed against an async StorageBackend protocol
    (`await self._storage.get_document_index_row_by_hash(...)`). The
    PostgresStorage wrapper in deps.storage is SYNC (wraps async via
    run_async_sync) -- passing it to the router would cause
    `await <sync-return-value>` -> TypeError NoneType can't be used in await
    expression. Architect live test 2026-06-29 crashed on this exact path.

    This shim awaits the underlying async ops in document_ops directly, so the
    router sees an async storage interface. Write-path ops are NOT proxied here
    -- the task body uses PostgresStorage sync ops outside the router for
    those, which is fine because they happen in our own asyncio context (we
    drove the router via asyncio.run already).
    """

    async def get_document_index_row_by_hash(self, file_hash):
        from core.src.storage.document_ops import get_document_index_row_by_hash as _g
        return await _g(file_hash)

    async def find_doc_id_slugs_for_item(self, delivery_item_id, doc_type):
        from core.src.storage.document_ops import find_doc_id_slugs_for_item as _f
        return await _f(delivery_item_id, doc_type)


def _resolve_doc_type_rules_path(customer_id: str):
    """Resolve doc_type_filename_rules.yaml per architect direction 2026-06-29.

    Lookup order (first existing wins):
      1. customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml  (canonical per [D-091])
      2. /app/customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml  (container bind-mount path)
      3. core/src/email_service/default_doc_type_rules.yaml                          (default; dev path)
      4. /app/core/src/email_service/default_doc_type_rules.yaml                      (default; container path)

    Returns the first existing Path, or the default path (which may or may not
    exist -- router opens it lazily and falls back to UNRESOLVED classification
    on read failure).
    """
    from pathlib import Path
    candidates = []
    if customer_id:
        candidates.extend([
            Path(f"customizations/template_schemas/{customer_id}/doc_type_filename_rules.yaml"),
            Path(f"/app/customizations/template_schemas/{customer_id}/doc_type_filename_rules.yaml"),
        ])
    candidates.extend([
        Path("core/src/email_service/default_doc_type_rules.yaml"),
        Path("/app/core/src/email_service/default_doc_type_rules.yaml"),
    ])
    for p in candidates:
        if p.exists():
            return p
    # None exist; return the last (default container path); router will
    # surface the read failure via UNRESOLVED + warning log.
    return candidates[-1]


def _build_ph1_router(deps, *, customer_id: str = ""):
    """Construct Fr52AttachmentRouter in Ph-1 first-pass mode (substring only).
    Returns None when prerequisites unavailable.

    Storage arg: passes an _AsyncStorageShim (NOT deps.storage) per the
    protocol-mismatch fix 2026-06-29. The shim async-wraps the 2 read ops
    the router awaits internally.

    Rules path: resolves per-customer via _resolve_doc_type_rules_path per
    architect direction 2026-06-29 -- previously hardcoded to default rules,
    which silently bypassed customer-specific classification rules.
    """
    try:
        from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
        rules_path = _resolve_doc_type_rules_path(customer_id)
        _log.info(
            "process_inbound_attachments: doc_type_rules_path=%s customer=%s exists=%s",
            rules_path, customer_id or "(none)", rules_path.exists(),
        )
        return Fr52AttachmentRouter(
            storage=_AsyncStorageShim(),    # async-shim per 2026-06-29 fix
            llm=None,                       # Ph-1 no LLM ROUTE_ATTACHMENT
            tg_resolver=None,
            doc_type_filename_rules_path=rules_path,
            plm_upload_enabled=False,
            review_required_enabled=False,
            ph1_first_pass_substring_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments: router construction failed: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
        return None


async def _persist_routed_attachment(
    *,
    deps,
    attachment,
    routed,
    candidate_items: list[dict],
    batch_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Steps E-H: write NSD bytes, insert DocumentIndexRow, insert N
    DocumentItemAssociation rows (FR-79 multi-item), increment doc_count_received
    per matched item, fire AttachmentReceived event per matched item.
    Returns counts for the caller's telemetry.
    """
    from core.src.storage.models import (
        DocumentIndexRow, DocumentItemAssociation, NSDPathType,
    )
    from core.src.template_schema.enums import IngestSource, DocType

    # Resolve NSD path. Choose constructor based on nsd_path_type + match count.
    nsd_path = _resolve_nsd_path(routed, attachment, candidate_items)
    if nsd_path is None:
        await _audit(deps, "attachment_path_resolution_failed", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "filename": getattr(attachment, "filename", "")[:120],
            "nsd_path_type": routed.nsd_path_type.value
                              if hasattr(routed.nsd_path_type, "value")
                              else str(routed.nsd_path_type),
            "correlation_id": correlation_id,
        })
        return {"match_count": 0, "item_ids": set(), "events_fired": 0}

    # Step E: write file bytes to NSD
    try:
        deps.storage.write_attachment_bytes(nsd_path, attachment.content)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "attachment write failed for file_hash=%s: %s: %s",
            routed.file_hash, type(exc).__name__, str(exc)[:120],
        )
        await _audit(deps, "attachment_write_failed", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "filename": getattr(attachment, "filename", "")[:120],
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            "correlation_id": correlation_id,
        })
        return {"match_count": 0, "item_ids": set(), "events_fired": 0}

    # Step F1: write DocumentIndexRow
    now = datetime.now(timezone.utc)
    primary_item_dict = None
    if routed.matches:
        for cand in candidate_items:
            if cand["item_id"] == routed.matches[0].item_id:
                primary_item_dict = cand
                break
    # milestone_id resolution -- architect 2026-06-29: unrouted attachments
    # previously fell through with milestone_id="" / NULL which made reset SQL
    # filters miss the row and dedup short-circuit later test runs.
    # Fix: always derive milestone_id from batch context. All items in a batch
    # share milestone_id by construction (batch is owner-scoped, owners are
    # TG-scoped within one milestone), so candidate_items[0] is authoritative.
    milestone_id = (
        (primary_item_dict or {}).get("milestone_id")
        or (candidate_items[0].get("milestone_id") if candidate_items else None)
        or ""
    )

    try:
        deps.storage.add_document_index_row(DocumentIndexRow(
            file_hash=routed.file_hash,
            milestone_id=milestone_id,
            doc_type=routed.doc_type,
            doc_id_slug=routed.doc_id_slug,
            rev_number=routed.rev_number,
            ingest_source=IngestSource.EMAIL.value,
            original_filename=getattr(attachment, "filename", ""),
            first_page_excerpt="",
            is_final=True,                            # Ph-1: all docs final by default
            inferred_tg_name=routed.inferred_tg_name,
            routing_resolution=routed.routing_resolution.value
                                if hasattr(routed.routing_resolution, "value")
                                else str(routed.routing_resolution),
            ingested_at=now,
        ))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "DocumentIndexRow insert failed for file_hash=%s: %s",
            routed.file_hash, str(exc)[:120],
        )
        await _audit(deps, "attachment_index_row_failed", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            "correlation_id": correlation_id,
        })
        # Continue -- the file bytes are written; try associations.

    # Step F2-H: per matched item -- association + increment + event
    item_ids: set[str] = set()
    events_fired = 0
    nsd_path_type_value = (routed.nsd_path_type.value
                            if hasattr(routed.nsd_path_type, "value")
                            else str(routed.nsd_path_type))
    local_nsd_path_str = "/".join(nsd_path.segments) if hasattr(nsd_path, "segments") else str(nsd_path)

    for match in routed.matches:
        item_dict = None
        for cand in candidate_items:
            if cand["item_id"] == match.item_id:
                item_dict = cand
                break
        if item_dict is None:
            continue
        try:
            deps.storage.add_document_item_association(DocumentItemAssociation(
                file_hash=routed.file_hash,
                delivery_item_id=match.item_id,
                milestone_id=item_dict.get("milestone_id") or "",
                local_nsd_path=local_nsd_path_str,
                nsd_path_type=nsd_path_type_value,
                owner_corp_id=item_dict.get("owner_corp_id") or "",
                owner_corp_usa_email=item_dict.get("owner_corp_usa_email"),
                owner_corp_email=item_dict.get("owner_corp_email"),
                owner_name=item_dict.get("owner_name"),
                associated_at=now,
                associated_by="auto:process_inbound_attachments",
            ))
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "DocumentItemAssociation insert failed for item=%s file=%s: %s",
                match.item_id, routed.file_hash, str(exc)[:120],
            )
            continue

        # Step G: increment doc_count_received
        try:
            new_count = deps.storage.increment_doc_count_received(match.item_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "doc_count_received increment failed for item=%s: %s",
                match.item_id, str(exc)[:120],
            )
            new_count = 0

        # Per-item audit
        await _audit(deps, "attachment_received", match.item_id, {
            "batch_id":       batch_id,
            "file_hash":      routed.file_hash,
            "filename":       getattr(attachment, "filename", "")[:120],
            "doc_type":       routed.doc_type,
            "nsd_path_type":  nsd_path_type_value,
            "new_doc_count":  new_count,
            "correlation_id": correlation_id,
        })
        item_ids.add(match.item_id)

        # Step H: fire AttachmentReceived TriggerEvent via dispatcher
        if _fire_attachment_received_event(deps, match.item_id, item_dict,
                                            routed, correlation_id, batch_id):
            events_fired += 1

    return {
        "match_count": len(routed.matches),
        "item_ids": item_ids,
        "events_fired": events_fired,
    }


def _resolve_nsd_path(routed, attachment, candidate_items: list[dict]):
    """Map (RoutedAttachment.nsd_path_type, matches, item context) -> NSDPath.
    Returns None when prerequisite item context is missing.
    """
    from core.src.storage.models import NSDPathType
    from core.src.storage.nsd import NSDPath

    primary_item = None
    if routed.matches:
        for cand in candidate_items:
            if cand["item_id"] == routed.matches[0].item_id:
                primary_item = cand
                break

    filename = getattr(attachment, "filename", "") or "file"
    pt_value = (routed.nsd_path_type.value
                if hasattr(routed.nsd_path_type, "value")
                else str(routed.nsd_path_type))

    if primary_item is None:
        # No item match -- DEFAULT_UNROUTED path. Use the FIRST candidate's
        # customer/device/milestone context as the rooting (Ph-1 batch scope
        # always implies single milestone).
        if not candidate_items:
            return None
        ctx = candidate_items[0]
        return NSDPath.internal_default_workitem(
            customer_id=ctx.get("customer_id") or "",
            device_id=ctx.get("device_id") or "",
            milestone_name=ctx.get("milestone_id") or "",
            inferred_tg_path_id=routed.inferred_tg_name or "_unknown_tg",
            original_filename=filename,
        )

    # Match found -- pick path constructor based on nsd_path_type
    customer_id = primary_item.get("customer_id") or ""
    device_id = primary_item.get("device_id") or ""
    milestone_name = primary_item.get("milestone_id") or ""
    tg_path_id = primary_item.get("tg_path_id") or primary_item.get("tg_name") or "_unknown_tg"
    item_path_id = primary_item.get("item_path_id") or f"item_{primary_item.get('item_no', 'x')}"

    if pt_value == NSDPathType.CLASSIFIED.value and routed.doc_id_slug and routed.rev_number:
        return NSDPath.internal_classified(
            customer_id, device_id, milestone_name, tg_path_id, item_path_id,
            routed.doc_type, routed.doc_id_slug, routed.rev_number,
        )
    if pt_value == NSDPathType.STAGED_NOT_REVISION.value:
        return NSDPath.internal_staged_revision(
            customer_id, device_id, milestone_name, tg_path_id, item_path_id,
            routed.doc_type, filename,
        )
    if pt_value == NSDPathType.STAGED_NOT_CLASSIFIED.value:
        return NSDPath.internal_staged_classification(
            customer_id, device_id, milestone_name, tg_path_id, item_path_id,
            filename,
        )
    # Fallback -- treat unknown as default_unrouted
    return NSDPath.internal_default_workitem(
        customer_id=customer_id, device_id=device_id, milestone_name=milestone_name,
        inferred_tg_path_id=routed.inferred_tg_name or tg_path_id or "_unknown_tg",
        original_filename=filename,
    )


def _fire_attachment_received_event(
    deps, delivery_item_id: str, item_dict: dict, routed, correlation_id: str,
    batch_id: str,
) -> bool:
    """Emit one AttachmentReceived TriggerEvent per matched item so rules
    advance_to_document_received_with_parser + reconcile_owner_intent_on_doc_count_reached
    can match. Returns True on success.
    """
    if deps.dispatcher is None:
        _log.info("attachment_received_event_skipped_no_dispatcher: item=%s",
                  delivery_item_id)
        return False
    try:
        from core.src.rule_engine import EntityRef, TriggerEvent, TriggerKind
        event = TriggerEvent(
            trigger=TriggerKind.ATTACHMENT_RECEIVED,
            sub_trigger=None,
            entity_ref=EntityRef(
                customer_id=item_dict.get("customer_id"),
                milestone_id=item_dict.get("milestone_id"),
                delivery_item_id=delivery_item_id,
            ),
            field_deltas=None,
            timestamp=datetime.now(timezone.utc),
            correlation_id=correlation_id,
            derived_fields={
                "file_hash": routed.file_hash,
                "doc_type":  routed.doc_type,
                "batch_id":  batch_id,
            },
        )
        deps.dispatcher.dispatch(event)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "AttachmentReceived dispatch failed for item=%s: %s",
            delivery_item_id, str(exc)[:120],
        )
        return False


async def _audit(deps, action_type: str, delivery_item_id, details: dict) -> None:
    """Bounded audit writer wrapper. Failure must not crash the task."""
    attribution = {
        "trigger_source": "automated",
        "correlation_id": details.get("correlation_id", ""),
        "modified_by":    "system:process_inbound_attachments",
    }
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution=attribution,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments audit failed action=%s: %s",
            action_type, str(exc)[:120],
        )
