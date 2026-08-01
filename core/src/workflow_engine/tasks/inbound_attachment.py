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

            # D-155 2026-07-26 — archives (.zip / .7z) are containers.
            # Outer archive gets NO router match / doc_type classification;
            # inner entries are extracted and each processed as an independent
            # attachment (own file_hash → own dedup + own routing).
            if _is_archive_attachment(attachment):
                arch_stats = await _process_archive_attachment(
                    deps=deps,
                    router=router,
                    attachment=attachment,
                    candidate_items=candidate_items,
                    batch_id=batch_id,
                    correlation_id=correlation_id,
                )
                processed += arch_stats["processed"]
                routed_with_match += arch_stats["routed_with_match"]
                routed_unrouted += arch_stats["routed_unrouted"]
                duplicates += arch_stats["duplicates"]
                failed += arch_stats["failed"]
                items_incremented.update(arch_stats["items_incremented"])
                events_fired += arch_stats["events_fired"]
                continue

            reg_stats = await _process_regular_attachment(
                deps=deps,
                router=router,
                attachment=attachment,
                candidate_items=candidate_items,
                batch_id=batch_id,
                correlation_id=correlation_id,
            )
            processed += reg_stats["processed"]
            routed_with_match += reg_stats["routed_with_match"]
            routed_unrouted += reg_stats["routed_unrouted"]
            duplicates += reg_stats["duplicates"]
            items_incremented.update(reg_stats["items_incremented"])
            events_fired += reg_stats["events_fired"]
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


def _is_archive_attachment(attachment) -> bool:
    """D-155 — archive containers are dispatched to _process_archive_attachment.
    Extension-only check (matches storage.archive_extractor.is_archive_filename)."""
    from core.src.storage.archive_extractor import is_archive_filename
    return is_archive_filename(getattr(attachment, "filename", "") or "")


async def _process_regular_attachment(
    *,
    deps,
    router,
    attachment,
    candidate_items: list[dict],
    batch_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Route + persist + view-tree one non-archive attachment.

    Returns telemetry deltas: processed, routed_with_match, routed_unrouted,
    duplicates, items_incremented (set), events_fired.

    Extracted 2026-07-26 for D-155 (was inline in the main loop). Behavior
    unchanged from prior code path for regular files.
    """
    routed = await router.route(attachment, batch_id, candidate_items)
    stats = {
        "processed": 1,
        "routed_with_match": 0,
        "routed_unrouted": 0,
        "duplicates": 0,
        "items_incremented": set(),
        "events_fired": 0,
    }
    # Cross-device shared-file fix 2026-07-07: is_duplicate=True means
    # the file bytes are already stored. If matches is empty as well,
    # it's a true no-op (all target items already carry an association
    # -- accidental resend). If matches is non-empty, the file is a
    # legitimate cross-device / cross-item re-use and the persist step
    # will skip the bytes write + index row but still create new
    # associations + increment doc_count_received for each match.
    if routed.is_duplicate and not routed.matches:
        stats["duplicates"] = 1
        await _audit(deps, "attachment_duplicate", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "filename": getattr(attachment, "filename", "")[:120],
            "correlation_id": correlation_id,
        })
        return stats

    if routed.is_duplicate:
        stats["duplicates"] = 1
        await _audit(deps, "attachment_duplicate_reroute", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "filename": getattr(attachment, "filename", "")[:120],
            "new_item_matches": [m.item_id for m in routed.matches],
            "correlation_id": correlation_id,
        })

    counts = await _persist_routed_attachment(
        deps=deps,
        attachment=attachment,
        routed=routed,
        candidate_items=candidate_items,
        batch_id=batch_id,
        correlation_id=correlation_id,
    )
    if counts["match_count"] > 0:
        stats["routed_with_match"] = 1
    else:
        stats["routed_unrouted"] = 1
    stats["items_incremented"] = counts["item_ids"]
    stats["events_fired"] = counts["events_fired"]

    try:
        await _write_matches_to_view_tree(
            attachment=attachment,
            matched_item_ids=counts.get("item_ids", set()),
            candidate_items=candidate_items,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments: view-tree write failed: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
    return stats


async def _process_archive_attachment(
    *,
    deps,
    router,
    attachment,
    candidate_items: list[dict],
    batch_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """D-155 — process an archive attachment (.zip / .7z).

    Steps:
      1. Persist an ARCHIVE_CONTAINER document_index audit row for the outer
         archive (no doc_type classification, no associations, no doc_count
         increment). Idempotent — re-sending same archive skips silently.
      2. Extract inner entries via storage.archive_extractor.
      3. On extract success: iterate inner entries. Each becomes a synthesized
         InboundAttachment (filename=inner path preserving subdirs, own hash)
         and runs through _process_regular_attachment — same routing + dedup
         + per-item association + view-tree writes.
      4. On extract failure (bad archive / password / oversized / library
         missing): audit event; outer archive is left in document_index for
         audit; no inner processing. TPM can see the outer arrived + why
         extraction failed.
      5. Save outer archive to view-tree in the TG(s) that received inner
         matches (so TPM sees "here's the archive these files came from").
         Skipped when no inner file matched — outer bytes reachable via NSD.

    Returns telemetry deltas aggregated across all inner entries. Outer archive
    itself does NOT contribute to routed_with_match / routed_unrouted counts —
    it's a container, not a routed document.
    """
    from core.src.email_service.protocol import InboundAttachment
    from core.src.storage.archive_extractor import extract_archive
    from core.src.storage.models import DocumentIndexRow, RoutingResolution
    from core.src.template_schema.enums import IngestSource

    stats = {
        "processed": 0,
        "routed_with_match": 0,
        "routed_unrouted": 0,
        "duplicates": 0,
        "failed": 0,
        "items_incremented": set(),
        "events_fired": 0,
    }

    filename = getattr(attachment, "filename", "") or ""
    content = getattr(attachment, "content", b"") or b""
    file_hash = getattr(attachment, "file_hash", "") or ""

    milestone_id = ""
    customer_id: str | None = None
    device_id: str | None = None
    if candidate_items:
        milestone_id = candidate_items[0].get("milestone_id") or ""
        # UR-2 (Ph-2 2026-08-01): scope carrier + device from the first
        # candidate; the whole batch shares customer + milestone by
        # construction. Device is per-item so this picks the first
        # candidate's device; sufficient for /_unknownTG which shows the
        # outer archive alongside its unrouted inner files.
        customer_id = candidate_items[0].get("customer_id")
        device_id = candidate_items[0].get("device_id")

    # Step 1: outer archive audit row — idempotent.
    try:
        deps.storage.add_document_index_row(DocumentIndexRow(
            file_hash=file_hash,
            milestone_id=milestone_id,
            customer_id=customer_id,
            device_id=device_id,
            doc_type="",                               # archive: no doc_type
            doc_id_slug=None,
            rev_number=None,
            ingest_source=IngestSource.EMAIL.value,
            original_filename=filename,
            first_page_excerpt="",
            is_final=False,                            # container, not deliverable
            inferred_tg_name=None,
            routing_resolution=RoutingResolution.ARCHIVE_CONTAINER.value,
            ingested_at=datetime.now(timezone.utc),
        ))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments: archive outer index row failed "
            "for file_hash=%s: %s: %s",
            file_hash, type(exc).__name__, str(exc)[:120],
        )

    # Step 2: extract.
    result = extract_archive(filename, bytes(content))

    await _audit(deps, "archive_container_received", None, {
        "batch_id": batch_id,
        "file_hash": file_hash,
        "filename": filename[:120],
        "extract_status": result.status,
        "extract_reason": result.reason[:200] if result.reason else "",
        "entry_count": len(result.entries),
        "correlation_id": correlation_id,
    })

    if result.status != "extracted":
        # Extraction failed — no inner processing. Outer already in
        # document_index. Save outer bytes to view-tree via a default-WI
        # TG so TPM can still download the archive.
        stats["failed"] = 1
        await _save_outer_archive_to_default_tg(
            attachment=attachment, candidate_items=candidate_items,
        )
        return stats

    # Step 3: iterate inner entries. Aggregate matched TGs so we can
    # replicate the outer archive to each of them (step 5).
    matched_tgs: set[tuple[str, str, str, str, str]] = set()
    for entry in result.entries:
        inner_rel = "/".join(entry.relative_parts)
        inner_bytes = entry.content
        inner_hash = _sha256_hex(inner_bytes)
        inner_attachment = InboundAttachment(
            filename=inner_rel,
            content=inner_bytes,
            content_type="application/octet-stream",
            file_hash=inner_hash,
        )
        try:
            inner_stats = await _process_regular_attachment(
                deps=deps,
                router=router,
                attachment=inner_attachment,
                candidate_items=candidate_items,
                batch_id=batch_id,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            _log.warning(
                "process_inbound_attachments: archive inner failure entry=%r "
                "file=%s: %s: %s",
                inner_rel, filename, type(exc).__name__, str(exc)[:120],
            )
            await _audit(deps, "archive_inner_processing_failed", None, {
                "batch_id": batch_id,
                "outer_filename": filename[:120],
                "inner_filename": inner_rel[:120],
                "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                "correlation_id": correlation_id,
            })
            continue

        stats["processed"] += inner_stats["processed"]
        stats["routed_with_match"] += inner_stats["routed_with_match"]
        stats["routed_unrouted"] += inner_stats["routed_unrouted"]
        stats["duplicates"] += inner_stats["duplicates"]
        stats["items_incremented"].update(inner_stats["items_incremented"])
        stats["events_fired"] += inner_stats["events_fired"]

        # Collect TG destinations of matched items (for outer replication).
        for item_id in inner_stats["items_incremented"]:
            for cand in candidate_items:
                if cand.get("item_id") == item_id and (
                    (cand.get("item_type") or "").lower() != "default"
                ):
                    key = (
                        cand.get("customer_id") or "",
                        cand.get("device_id") or "",
                        cand.get("milestone_id") or "",
                        cand.get("tg_name") or "",
                        cand.get("item_type") or "",
                    )
                    if key[3]:
                        matched_tgs.add(key)
                    break

    # Step 5: replicate outer archive to each matched TG's view-tree root
    # so TPM can see "here's the archive these files came from".
    if matched_tgs:
        await _replicate_outer_archive_to_tgs(
            filename=filename, content=bytes(content), targets=matched_tgs,
        )

    return stats


def _sha256_hex(data: bytes) -> str:
    """Local helper — sha256 of bytes as hex string."""
    import hashlib
    return hashlib.sha256(data).hexdigest()


async def _save_outer_archive_opaque(
    *, customer_id: str, device_id: str, milestone_id: str,
    tg_name: str, filename: str, content: bytes,
) -> None:
    """Save the outer archive bytes to view-tree AS AN OPAQUE FILE (no
    extraction). Bypasses write_attachment_to_view_tree because that helper
    would re-extract the same archive. Best-effort — logs on failure.

    Placed at TG root as `<filename>`. Overwrite = new version via
    save_view_document. tg_name empty → skip (matches view-tree contract).
    """
    if not tg_name:
        return
    from core.src.storage.document_view_ops import save_view_document
    try:
        await save_view_document(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, tg_name=tg_name,
            relative_parts=(filename,),
            content=content, saved_by="auto", source="archive_container",
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "process_inbound_attachments: outer-archive opaque save to "
            "tg=%s file=%s failed: %s: %s",
            tg_name, filename, type(exc).__name__, str(exc)[:120],
        )


async def _save_outer_archive_to_default_tg(
    *, attachment, candidate_items: list[dict],
) -> None:
    """When extraction fails, save the outer archive to view-tree at the
    first candidate item's TG (or an unrouted-archives fallback name) so
    TPM can still access the file. Best-effort."""
    if not candidate_items:
        return
    ctx = candidate_items[0]
    await _save_outer_archive_opaque(
        customer_id=ctx.get("customer_id") or "",
        device_id=ctx.get("device_id") or "",
        milestone_id=ctx.get("milestone_id") or "",
        tg_name=ctx.get("tg_name") or "_unrouted_archives",
        filename=getattr(attachment, "filename", "") or "archive.bin",
        content=bytes(getattr(attachment, "content", b"") or b""),
    )


async def _replicate_outer_archive_to_tgs(
    *, filename: str, content: bytes, targets: set,
) -> None:
    """Save the outer archive bytes AS-IS (no extraction) to view-tree at
    the root of each matched TG. Called after inner-entry processing so the
    archive appears alongside its extracted contents in the browse UI.
    Best-effort per-TG.
    """
    for cust, dev, mil, tg, _item_type in targets:
        await _save_outer_archive_opaque(
            customer_id=cust, device_id=dev, milestone_id=mil,
            tg_name=tg, filename=filename, content=content,
        )


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

    # JIT back-fill: if any item is missing tg_path_id, item_path_id, OR
    # item_description, do ONE SP batch read for the whole milestone+device
    # and patch local rows. Best-effort -- on SP failure we fall back to
    # tg_name + item_no synthesized paths and skip routing for that item.
    # Architect 2026-06-29: extended to include item_description because
    # the test flow starts from OutreachSent (skipping kickoff back-fill),
    # so existing items' NULL item_description never gets refreshed unless
    # we do it here at attachment time.
    # Also back-fill doc_count + review_required which the SP_ALERT body
    # parse doesn't carry today. Same SP read covers all 5 fields.
    # Architect 2026-06-29: "postgres should pull doc_count, review_required
    # fields from SP - can you please check".
    missing = [
        (item_id, item) for item_id, item in items_by_id.items()
        if (
            not getattr(item, "tg_path_id", None)
            or not getattr(item, "item_path_id", None)
            or getattr(item, "item_description", None) in (None, [], "")
            or getattr(item, "doc_count", None) in (None, 0)
            or getattr(item, "review_required", None) is None
        )
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
                    sp_item_description_raw = sp_row.get("item_description")
                    sp_doc_count = sp_row.get("doc_count")
                    sp_review_required = sp_row.get("review_required")
                    if sp_tg_path_id and not getattr(item, "tg_path_id", None):
                        updates["tg_path_id"] = sp_tg_path_id
                    if sp_item_path_id and not getattr(item, "item_path_id", None):
                        updates["item_path_id"] = sp_item_path_id
                    if (
                        sp_item_description_raw is not None
                        and getattr(item, "item_description", None) in (None, [], "")
                    ):
                        # Reuse the same parser as sp_alert_imports for shape
                        # consistency. ast.literal_eval handles both list and
                        # string-of-list inputs.
                        from core.src.workflow_engine.tasks.sp_alert_imports import (
                            _parse_item_description,
                        )
                        parsed = _parse_item_description(sp_item_description_raw)
                        if parsed is not None:
                            updates["item_description"] = parsed
                    # doc_count back-fill per architect 2026-06-29 protection rule:
                    # "any non-zero value in postgres is final; never write 0".
                    # Reason: subsequent SP CHANGE alerts (e.g. on state change
                    # NotStarted->Open->OutreachSent) may re-read the row and
                    # return doc_count=0 if SP hasn't propagated the prior edit
                    # to its alert payload yet. Writing 0 over a known-good
                    # non-zero would corrupt the state machine's doc_count_reached
                    # gate.
                    # Rule: write doc_count only when (a) SP returns non-zero AND
                    # (b) local is None or 0. SP returning 0 is always ignored.
                    try:
                        sp_doc_count_int = int(sp_doc_count) if sp_doc_count is not None else None
                    except (TypeError, ValueError):
                        sp_doc_count_int = None
                    local_doc_count = getattr(item, "doc_count", None)
                    if (
                        sp_doc_count_int is not None
                        and sp_doc_count_int > 0
                        and local_doc_count in (None, 0)
                    ):
                        updates["doc_count"] = sp_doc_count_int
                    # review_required: Yes/No Choice on SP, may arrive as bool
                    # or string. Coerce defensively.
                    if sp_review_required is not None and getattr(item, "review_required", None) is None:
                        if isinstance(sp_review_required, bool):
                            updates["review_required"] = sp_review_required
                        elif isinstance(sp_review_required, str):
                            updates["review_required"] = sp_review_required.strip().lower() in ("yes", "true", "1")
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

    async def item_has_association(self, file_hash, delivery_item_id):
        # Added 2026-07-07 for cross-device shared-file fix. Router calls this
        # inside Step 0 to filter out items that already carry an association
        # for a re-arriving file hash.
        from core.src.storage.document_ops import item_has_association as _i
        return await _i(file_hash, delivery_item_id)


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

    # Resolve NSD path. When is_duplicate=True the file bytes are already on
    # disk from a prior arrival; reuse that ORIGINAL path so new associations
    # point at the actual location (otherwise submit_to_carrier would try to
    # upload from a path where nothing was ever written).
    nsd_path = None
    if routed.is_duplicate:
        try:
            existing_path = deps.storage.get_local_nsd_path_for_file_hash(routed.file_hash)
        except Exception:  # noqa: BLE001
            existing_path = None
        if existing_path:
            # Reconstruct NSDPath from the stored relative path so downstream
            # segment logic works identically to the fresh-write case.
            try:
                from core.src.storage.nsd import NSDPath
                nsd_path = NSDPath.from_relative(existing_path)
            except Exception:  # noqa: BLE001
                nsd_path = None
    if nsd_path is None:
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

    # Step E: write file bytes to NSD (skip when duplicate -- bytes already
    # stored on disk from a prior arrival; re-writing would be wasteful I/O
    # and re-writing to a per-device path would create duplicate copies).
    if not routed.is_duplicate:
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
    # UR-2 (Ph-2 2026-08-01): customer + device for /_unknownTG scoping. Same
    # resolution as milestone above -- primary match wins, else first
    # candidate. Left None (nullable column) when neither is available,
    # which means the row won't show up in the /_unknownTG UI.
    customer_id = (
        (primary_item_dict or {}).get("customer_id")
        or (candidate_items[0].get("customer_id") if candidate_items else None)
    )
    device_id = (
        (primary_item_dict or {}).get("device_id")
        or (candidate_items[0].get("device_id") if candidate_items else None)
    )

    # Architect 2026-06-30: bounded retry on transient asyncpg session blips
    # (STR-E001). Each storage op opens its own session, so a one-off connection
    # failure here is retryable -- live evidence: same-batch association write
    # immediately after succeeded against a fresh session, proving the DB came
    # back. On final failure, return early -- a missing index row with a
    # successful association row creates an orphan (file_hash present in
    # document_item_association, absent in document_index) that breaks every
    # downstream JOIN (FR-7 doc_count gate, FR-66 is_final cascade,
    # get_documents_for_item, list_revisions).
    #
    # Cross-device fix 2026-07-07: skip the insert entirely on duplicate.
    # add_document_index_row is already idempotent (returns silently when the
    # row exists), so the skip is telemetry hygiene rather than correctness.
    _index_row_attempts = 3
    last_index_exc: Exception | None = None
    if routed.is_duplicate:
        _index_row_attempts = 0
    for _attempt in range(_index_row_attempts):
        try:
            deps.storage.add_document_index_row(DocumentIndexRow(
                file_hash=routed.file_hash,
                milestone_id=milestone_id,
                customer_id=customer_id,
                device_id=device_id,
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
            last_index_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_index_exc = exc
            if _attempt < _index_row_attempts - 1:
                await asyncio.sleep(0.2 * (2 ** _attempt))  # 0.2s, 0.4s
    if last_index_exc is not None:
        _log.warning(
            "DocumentIndexRow insert failed after %d attempts for file_hash=%s: %s",
            _index_row_attempts, routed.file_hash, str(last_index_exc)[:120],
        )
        await _audit(deps, "attachment_index_row_failed", None, {
            "batch_id": batch_id, "file_hash": routed.file_hash,
            "error": f"{type(last_index_exc).__name__}: {str(last_index_exc)[:120]}",
            "attempts": _index_row_attempts,
            "correlation_id": correlation_id,
        })
        return {"match_count": 0, "item_ids": set(), "events_fired": 0}

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

        # Step H: INLINE state advance OutreachSent -> DocumentReceived per
        # architect 2026-06-30 decision. Previously this fired
        # AttachmentReceived -> rule_engine -> update_state Celery task, which
        # was asynchronous + raced against owner_reply task that also wanted
        # to update state. The new chain (attachment -> owner_reply in
        # email_polling) only works if the state advance is COMPLETE before
        # the next chain task fires; rule_engine's async dispatch broke that
        # guarantee. By calling tracker.transitions.update_delivery_state
        # synchronously here, state advance commits before this task returns
        # and the chain's next link (apply_owner_reply) sees fresh state.
        #
        # Ph-1 scope: only OutreachSent -> DocumentReceived. Other current
        # states (already past DocumentReceived) get the doc_count_received
        # update + audit, but no state change. Regression-to-DocumentReceived
        # for UnderPMReview/OwnerClosed (TPM-upload-late case) is Ph-2 work
        # gated on owner_confirmed_closed field + reconcile-rule rework.
        try:
            fresh_item = deps.storage.get_delivery_item(match.item_id)
        except Exception:  # noqa: BLE001
            fresh_item = None
        if fresh_item is not None:
            current_state = getattr(fresh_item, "delivery_state", None)
            doc_count = getattr(fresh_item, "doc_count", 0) or 0
            doc_count_received_fresh = getattr(fresh_item, "doc_count_received", 0) or 0
            doc_count_reached = doc_count_received_fresh >= doc_count
            if doc_count_reached and current_state == "OutreachSent":
                try:
                    from core.src.tracker.transitions import update_delivery_state
                    from core.src.template_schema.enums import DeliveryState
                    update_delivery_state(
                        delivery_item_id=match.item_id,
                        target_state=DeliveryState.DOCUMENT_RECEIVED,
                        params={},
                        event_context={
                            "correlation_id": correlation_id,
                            "trigger_source": "automated",
                            "modified_by": "system:process_inbound_attachments",
                        },
                        storage=deps.storage,
                        sp_writer=deps.sp_writer,
                        audit=deps.audit,
                    )
                    events_fired += 1
                except Exception as exc:  # noqa: BLE001
                    _log.warning(
                        "inline OutreachSent->DocumentReceived advance failed "
                        "for item=%s: %s: %s",
                        match.item_id, type(exc).__name__, str(exc)[:120],
                    )
            elif doc_count_reached:
                # Item already past DocumentReceived (OwnerClosed/UnderPMReview/
                # ReadyForSubmission/SubmittedToCustomer/Closed). Count the doc
                # via the existing increment + audit; no state change Ph-1.
                await _audit(deps, "supplementary_doc_received", match.item_id, {
                    "current_state":      current_state,
                    "doc_count_received": doc_count_received_fresh,
                    "doc_count":          doc_count,
                    "correlation_id":     correlation_id,
                })

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
            original_filename=filename,
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


async def _write_matches_to_view_tree(
    *,
    attachment,
    matched_item_ids: set,
    candidate_items: list[dict],
) -> None:
    """D-150 Chunk 3 hook: persist attachment bytes to the HILDA-side documents
    view tree, one write per DISTINCT (customer, device, milestone, tg_name)
    across the matched items.

    Filters:
      * Only items whose item_id appears in matched_item_ids are considered.
      * Items with item_type='default' are dropped inside write_attachment_to_view_tree.
      * Items with empty tg_name are dropped inside write_attachment_to_view_tree.

    Best-effort: individual writes' failures are logged inside
    write_attachment_to_view_tree; this outer loop keeps going.
    """
    from core.src.storage import write_attachment_to_view_tree

    if not matched_item_ids:
        return
    content = getattr(attachment, "content", None)
    filename = getattr(attachment, "filename", None) or "attachment.bin"
    if content is None or not isinstance(content, (bytes, bytearray)):
        return

    seen: set[tuple[str, str, str, str]] = set()
    for item in candidate_items:
        item_id = item.get("item_id") if isinstance(item, dict) else getattr(item, "item_id", None)
        if not item_id or item_id not in matched_item_ids:
            continue
        cust = _row_field(item, "customer_id") or ""
        dev = _row_field(item, "device_id") or ""
        mil = _row_field(item, "milestone_id") or ""
        tg = _row_field(item, "tg_name") or ""
        item_type = _row_field(item, "item_type") or ""
        key = (cust, dev, mil, tg)
        if key in seen:
            continue
        seen.add(key)
        await write_attachment_to_view_tree(
            customer_id=cust, device_id=dev, milestone_id=mil, tg_name=tg,
            item_type=item_type, filename=filename, content=bytes(content),
            saved_by="auto",
        )


def _row_field(row, key: str):
    """Read a field from either a dict-shaped or dataclass/pydantic-shaped item row."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


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
