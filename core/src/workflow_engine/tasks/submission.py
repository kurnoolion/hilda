"""submission.py: ESCALATE + START_ITEM_COLLECTION + QUEUE_SUBMISSION task bodies.

- ESCALATE per FR-10 -- delegates to messenger (corp messenger DM) when wired.
- START_ITEM_COLLECTION per FR-8 -- transitions delivery_state via tracker;
  optionally chains SEND_INITIAL_OUTREACH (rule_engine chains this Ph-2; Ph-1
  is single-action audit-write).
- QUEUE_SUBMISSION per FR-77 + FR-19 -- delegates to customer_adapter
  (Google Drive upload via [D-116] thin wrapper) when wired.

Each task gracefully degrades to audit-only when its downstream dep is None.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.src.rule_engine import ActionKind

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "escalate_task",
    "start_item_collection_task",
    "queue_submission_task",
]

_log = logging.getLogger(__name__)


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.submission.escalate")
def escalate_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """ESCALATE per FR-10 cross-channel -- send corp messenger DM when reminder
    cadence exhausted.

    params:
      - channel:               str (default "corp_messenger")
      - escalation_template:   str (e.g., "tg_lead_escalation")
      - escalation_reason:     str (bounded enum per NFR-2: "reminder_cadence_exhausted",
                                   "deadline_proximity", "owner_unreachable")

    event_context: standard + owner_corp_id (corp messenger DM target).
    """
    deps = get_task_deps()
    channel = params.get("channel", "corp_messenger")
    template = params.get("escalation_template", "tg_lead_escalation")
    reason = params.get("escalation_reason", "reminder_cadence_exhausted")
    owner_corp_id = event_context.get("owner_corp_id")
    delivery_item_id = event_context.get("delivery_item_id")

    delivered = False
    if channel == "corp_messenger" and deps.messenger is not None and owner_corp_id:
        try:
            delivered = _send_messenger(
                deps,
                owner_corp_id=owner_corp_id,
                message=f"HILDA escalation -- {reason}: deliverable {delivery_item_id} needs attention.",
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("escalate messenger send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="escalate",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "channel":             channel,
            "template":            template,
            "escalation_reason":   reason,
            "owner_corp_id":       owner_corp_id,
            "milestone_id":        event_context.get("milestone_id"),
            "delivered":           delivered,
            "send_skipped":        not delivered,
        },
    )
    return {
        "channel":           channel,
        "escalation_reason": reason,
        "owner_corp_id":     owner_corp_id,
        "delivered":         delivered,
        "outcome":           "delivered" if delivered else "audit_only",
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.submission.start_item_collection")
def start_item_collection_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """START_ITEM_COLLECTION per FR-8 -- transitions delivery_state to "Outreach Sent"
    + writes audit log. Chained outreach is the rule_engine's responsibility (action 1
    in StartCollection rule actions list per defaults.yaml worked example).

    params:
      - target_state: str (default "Outreach Sent" per D-124 DeliveryState α lock)

    event_context: standard.
    """
    deps = get_task_deps()
    target_state = params.get("target_state", "Outreach Sent")
    delivery_item_id = event_context.get("delivery_item_id")
    if delivery_item_id is None:
        raise ValueError("START_ITEM_COLLECTION requires delivery_item_id in event_context")

    deps.audit.write_communication_log(
        action_type="start_item_collection",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    event_context.get("pm_id", "system"),
        },
        details={
            "target_state":  target_state,
            "milestone_id":  event_context.get("milestone_id"),
        },
    )
    return {
        "delivery_item_id": delivery_item_id,
        "target_state":     target_state,
        "outcome":          "audit_written",
    }


def _resolve_upload_params(
    deps,
    params: dict[str, Any],
    delivery_item_id: str | None,
) -> dict[str, Any]:
    """Resolve the 4 upload params (source_dir, filename, target_dir,
    customer_delivery_info) per precedence:
      1. Explicit params override (rule YAML can pin any of the 4)
      2. Storage DeliveryItem for target_dir (item.target_folder per FR-77)
         + customer_delivery_info (per-item per D-126 cascade)
      3. Storage documents-for-item lookup for source_dir + filename --
         picks the most recently ingested final doc per FR-66 single-revision
         Ph-1 (item.is_final=True; in early drop all docs become final
         automatically).

    Added 2026-06-27 per architect rule-walk-through Section 4 Chunk B:
    queue_submission_task previously required all 4 params from the rule
    YAML; Rule 4-3 (advance_to_ready_for_submission_on_pm_approval) passes
    only `channel: customer_adapter`. Task fell through to audit-only
    (would-have-uploaded), silently skipping the carrier upload.

    Mirrors the outreach _resolve_recipient pattern from commit 4d138f3 --
    storage is authoritative; params override for tests / TPM-manual cases.

    Returns dict with 4 keys; values may be None when not resolvable
    (caller decides whether to attempt upload based on what's populated).
    """
    resolved: dict[str, Any] = {
        "source_dir":             params.get("source_dir"),
        "filename":               params.get("filename"),
        "target_dir":             params.get("target_dir"),
        "customer_delivery_info": params.get("customer_delivery_info"),
    }
    if delivery_item_id is None:
        return resolved

    # Item-level fields (target_folder + customer_delivery_info)
    try:
        item = deps.storage.get_delivery_item(delivery_item_id)
        if resolved["target_dir"] is None:
            resolved["target_dir"] = getattr(item, "target_folder", None) or ""
        if resolved["customer_delivery_info"] is None:
            resolved["customer_delivery_info"] = (
                getattr(item, "customer_delivery_info", None) or ""
            )
    except Exception:  # noqa: BLE001 -- non-fatal; fall through
        pass

    # Document path (source_dir + filename) -- requires duck-typed lookup
    # because storage methods may be sync OR async (storage/document_ops.py
    # is async; tests use sync mocks).
    if resolved["source_dir"] is not None and resolved["filename"] is not None:
        return resolved

    lookup = getattr(deps.storage, "get_documents_for_item", None)
    if lookup is None:
        return resolved
    try:
        result = lookup(delivery_item_id)
        # Detect coroutine -> run sync via asyncio.run
        import inspect
        if inspect.iscoroutine(result):
            import asyncio
            result = asyncio.run(result)
        docs = list(result or [])
    except Exception:  # noqa: BLE001 -- non-fatal
        return resolved

    if not docs:
        return resolved

    # FR-66 single-revision Ph-1: prefer is_final=True; fall back to most
    # recent. Docs ordered by ingested_at per document_ops.get_documents_for_item.
    final_docs = [d for d in docs if getattr(d, "is_final", False)]
    chosen = (final_docs or docs)[-1]

    # Document path lookup needs both DocumentIndexRow and association rows;
    # for Ph-1, we use the most recently associated local_nsd_path. If the
    # chosen doc carries an attribute named `local_nsd_path` (Mock-style), use
    # it directly; otherwise look up the first association via
    # get_documents_for_item_associations duck-typed method.
    local_nsd_path = getattr(chosen, "local_nsd_path", None)
    if local_nsd_path is None:
        # Try association lookup; if absent, skip path resolution
        assoc_lookup = getattr(deps.storage, "get_document_associations_for_item", None)
        if assoc_lookup is not None:
            try:
                assoc_result = assoc_lookup(delivery_item_id)
                import inspect
                if inspect.iscoroutine(assoc_result):
                    import asyncio
                    assoc_result = asyncio.run(assoc_result)
                assocs = list(assoc_result or [])
                if assocs:
                    local_nsd_path = getattr(assocs[-1], "local_nsd_path", None)
            except Exception:  # noqa: BLE001
                pass

    if local_nsd_path:
        from pathlib import Path as _P
        p = _P(local_nsd_path)
        if resolved["source_dir"] is None:
            resolved["source_dir"] = str(p.parent)
        if resolved["filename"] is None:
            resolved["filename"] = p.name
    else:
        # Fall back to original_filename when path missing
        if resolved["filename"] is None:
            resolved["filename"] = getattr(chosen, "original_filename", None)

    return resolved


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.submission.queue_submission")
def queue_submission_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """QUEUE_SUBMISSION per FR-77 + FR-19 -- uploads document to carrier portal
    via customer_adapter (Google Drive thin wrapper per [D-116]).

    params:
      - channel:                  str (default "customer_adapter")
      - source_dir:               str (LOCAL NSD path containing the file)
      - filename:                 str (basename; the file to upload)
      - target_dir:               str (subdirectory under <customer_delivery_info>)
      - customer_delivery_info:   str (per-row from Deliverables SP list per D-126;
                                       e.g. "drive.google.com/<project>/<root>")

    event_context: standard + device_id, milestone_name.

    Note Ph-1: gated upstream by no_customer_upload=False per FR-80; this task
    body assumes it's been invoked for an item that requires upload.
    """
    deps = get_task_deps()
    device_id = event_context.get("device_id", event_context.get("project_model", ""))
    milestone_name = event_context.get("milestone_name", "")
    delivery_item_id = event_context.get("delivery_item_id")
    # Resolve via params + storage chain per [D-080]-style precedence
    # (added 2026-06-27 per rule-walk-through Chunk B).
    resolved = _resolve_upload_params(deps, params, delivery_item_id)
    source_dir = resolved["source_dir"]
    filename = resolved["filename"]
    target_dir = resolved["target_dir"] or ""
    customer_delivery_info = resolved["customer_delivery_info"] or ""

    upload_success = False
    error_code = None
    if deps.customer_adapter is not None and source_dir and filename:
        try:
            result = _upload_attachment(
                deps,
                device_id=device_id,
                milestone_name=milestone_name,
                source_dir=Path(source_dir),
                target_dir=target_dir,
                filename=filename,
                customer_delivery_info=customer_delivery_info,
            )
            upload_success = bool(getattr(result, "success", False))
            error_code = getattr(result, "error_code", None)
        except Exception as e:  # noqa: BLE001
            _log.warning("queue_submission upload failed: %s", type(e).__name__)
            error_code = "WFL-UPLOAD-EXCEPTION"

    deps.audit.write_communication_log(
        action_type="queue_submission",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "device_id":              device_id,
            "milestone_name":         milestone_name,
            "filename":               filename,
            "target_dir":             target_dir,
            "customer_delivery_info": customer_delivery_info,
            "upload_success":         upload_success,
            "error_code":             error_code,
            "send_skipped":           deps.customer_adapter is None,
        },
    )
    return {
        "device_id":      device_id,
        "milestone_name": milestone_name,
        "filename":       filename,
        "upload_success": upload_success,
        "error_code":     error_code,
        "outcome":        "uploaded" if upload_success else ("audit_only" if deps.customer_adapter is None else "failed"),
    }


# ---------------------------------------------------------------------------
# Internal -- sync bridges for async downstream Protocols
# ---------------------------------------------------------------------------


def _send_messenger(deps: Any, *, owner_corp_id: str, message: str) -> bool:
    import asyncio
    coro = deps.messenger.send(owner_corp_id, message)
    return _run_sync(coro)


def _upload_attachment(
    deps: Any,
    *,
    device_id: str,
    milestone_name: str,
    source_dir: Path,
    target_dir: str,
    filename: str,
    customer_delivery_info: str,
) -> Any:
    import asyncio
    coro = deps.customer_adapter.upload_attachment(
        device_id=device_id,
        milestone_name=milestone_name,
        source_dir=source_dir,
        target_dir=target_dir,
        filename=filename,
        customer_delivery_info=customer_delivery_info,
    )
    return _run_sync(coro)


def _run_sync(coro: Any) -> Any:
    """Sync-bridge from async Protocol method to Celery sync body."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return loop.run_until_complete(coro)
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


register_task_binding(TaskBinding(
    action_kind=ActionKind.ESCALATE,
    celery_task=escalate_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.START_ITEM_COLLECTION,
    celery_task=start_item_collection_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.QUEUE_SUBMISSION,
    celery_task=queue_submission_task,
    queue="default",
))
