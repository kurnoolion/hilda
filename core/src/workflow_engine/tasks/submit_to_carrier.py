"""submit_to_carrier.py -- SubmitToCarrier ActionKind task per architect
2026-06-30 design pass.

Milestone-scoped orchestrator triggered by
submit_to_carrier_on_milestone_submission_triggered rule when TPM clicks
Submit-to-Carrier in the SP UI and milestone_submission_triggered_at gets
set on the Milestone row. Pattern A (SP-authoritative) per [D-068] -- HILDA
trusts the SP-side button visibility guard (all items in RFS + no_customer_upload
handled by SP UI engineer); no HILDA-side pre-check duplication.

For each delivery item in the milestone:
  - Skip if delivery_state == SubmittedToCustomer (idempotency; task restart)
  - Skip if delivery_state != ReadyForSubmission (out-of-scope for this event)
  - Skip if no_customer_upload==True OR target_folder is None (Confirmation
    + default WI + any per-item override that opts out of upload)
  - List classified DocumentItemAssociation rows (nsd_path_type=classified only)
  - Skip if zero classified files (audit as skip_no_files)
  - Sequentially upload each file via deps.customer_adapter.upload_attachment
    (async under the hood; wrapped with a fresh event loop per Celery task
    per _run_sync bridge convention). source_dir composed as
    <nsd_volume_prefix> + <dirname(local_nsd_path)>; filename = basename.
  - Per-file outcome:
      - True  -> log ok, continue
      - False -> post-verify failed; audit, continue to next file (item stays RFS)
      - raise -> browser/session dead; abort whole task; Celery retries
  - Per-item all True -> transition ReadyForSubmission -> SubmittedToCustomer

Retry policy (architect lock 2026-06-30):
  max_retries=2, retry_backoff=180s, retry_backoff_max=300s, jitter -- 3 total
  attempts at ~t=0, ~t=3min, ~t=8min. On final failure, task moves to Celery
  FAILURE state; the terminal-failure audit stays via what got written on each
  attempt; items remain in ReadyForSubmission (PM dashboard surfaces stuck
  milestone next poll).

Note: the customer_adapter's uploadAttachment (via CustomerAdapter Protocol
+ GoogleDriveBaseAdapter per [D-116]) already exhausts internal retries for
the network/selenium/MFA layer before it returns; HILDA's Celery-level retry
is a safety net for genuine outer-loop crashes (worker restart mid-task,
process kill, redis blip), NOT a retry for adapter-internal transient issues.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["submit_to_carrier_task"]

_log = logging.getLogger(__name__)


# --- constants (architect lock 2026-06-30) -----------------------------------
_TARGET_STATE_VALUE  = "SubmittedToCustomer"     # DeliveryState enum value
_REQUIRED_FROM_STATE = "ReadyForSubmission"      # DeliveryState enum value


@hilda_celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=2,                   # 3 total attempts (initial + 2 retries)
    retry_backoff=180,               # 180s base, doubled per retry (capped)
    retry_backoff_max=300,           # cap individual delay at 5 minutes
    retry_jitter=True,               # spread across parallel milestones
    name="core.src.workflow_engine.tasks.submit_to_carrier.submit_to_carrier",
)
def submit_to_carrier_task(
    self,
    params: dict[str, Any],
    event_context: dict[str, Any],
) -> dict[str, Any]:
    """SubmitToCarrier -- milestone-scoped upload orchestrator.

    params: unused (rule passes empty dict).
    event_context: standard + customer_id + milestone_id from the
                   Milestones-list CHANGED alert. delivery_item_id is None
                   (milestone-level trigger).

    Returns a summary dict keyed by outcome + per-item counts for telemetry.
    """
    deps = get_task_deps()
    correlation_id = event_context.get("correlation_id", "")
    customer_id    = event_context.get("customer_id")
    milestone_id   = event_context.get("milestone_id")
    # device_id scoping per fix 2026-07-06: SP Milestones has ONE row per
    # (customer, device, milestone) triple; TPM's Submit-to-Carrier click
    # updates ONE row -> ONE alert -> HILDA should upload only THAT device's
    # items. Without device_id filter, all devices' items in the milestone
    # get uploaded on a single-device Submit click.
    device_id      = event_context.get("device_id")

    # -- Identity guard -------------------------------------------------------
    if not customer_id or not milestone_id:
        _log.warning(
            "submit_to_carrier_skip_missing_identity: customer_id=%r milestone_id=%r "
            "correlation_id=%s",
            customer_id, milestone_id, correlation_id,
        )
        return {"outcome": "skipped_missing_identity", "uploaded_items": 0}

    # -- Storage / adapter presence -------------------------------------------
    list_method = getattr(deps.storage, "list_items_for_milestone", None)
    if list_method is None:
        _log.warning(
            "submit_to_carrier_skip_no_storage: milestone_id=%s correlation_id=%s",
            milestone_id, correlation_id,
        )
        return {"outcome": "skipped_no_storage", "uploaded_items": 0}
    if deps.customer_adapter is None:
        _log.warning(
            "submit_to_carrier_skip_no_adapter: milestone_id=%s correlation_id=%s",
            milestone_id, correlation_id,
        )
        _audit(deps, "submit_to_carrier_skipped", None, {
            "reason":         "customer_adapter_not_wired",
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "correlation_id": correlation_id,
        })
        return {"outcome": "skipped_no_adapter", "uploaded_items": 0}

    # -- Item iteration -------------------------------------------------------
    items = list_method(milestone_id, None) or []
    # Apply device_id filter per fix 2026-07-06 (see identity block above).
    if device_id:
        items = [
            it for it in items
            if (getattr(it, "device_id", None) or "") == device_id
        ]
    if not items:
        _log.info(
            "submit_to_carrier_empty_milestone: customer_id=%s milestone_id=%s device_id=%s",
            customer_id, milestone_id, device_id,
        )
        return {"outcome": "fired", "items_scanned": 0, "uploaded_items": 0}

    # Resolve host-side NSD prefix from CustomerAdapterConfig (architect lock
    # 2026-06-30 -- container-vs-host topology stays in config, not task code).
    nsd_prefix = _resolve_nsd_volume_prefix(deps)

    scanned          = len(items)
    skipped_state    = 0    # not in ReadyForSubmission
    skipped_already  = 0    # already SubmittedToCustomer
    skipped_upload   = 0    # no_customer_upload OR target_folder null
    skipped_no_files = 0    # zero classified files
    uploaded_items   = 0    # work items whose ALL files posted OK + transitioned
    partial_items    = 0    # at least one False -- item stays in RFS
    # SUBMIT-STATS-1 (2026-08-28): milestone-level file totals were previously
    # discarded after each item loop (only `uploaded_items` survived), which
    # made the log line "uploaded=5" ambiguous when 12 files had actually
    # posted across those 5 items. Track per-file counters so stats reflect
    # what actually hit the carrier.
    files_uploaded_total = 0
    files_failed_total   = 0
    # DRRP1-1 chunk 3 (2026-09-01): files contributed by ANOTHER milestone's
    # work-item per milestone_item_mapping. Counted separately so the tick log
    # distinguishes "this milestone collected 5 documents" from "3 of these came
    # from DRR" -- without that split, a migration failure looks like a quiet
    # drop in the total rather than a broken mapping.
    files_uploaded_migrated = 0
    files_failed_migrated   = 0
    items_with_migrated     = 0

    for item in items:
        item_id       = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
        state         = getattr(item, "delivery_state", None) or ""
        target_folder = getattr(item, "target_folder", None)
        no_upload     = bool(getattr(item, "no_customer_upload", False))

        if state == _TARGET_STATE_VALUE:
            skipped_already += 1
            _log.info(
                "submit_to_carrier_skip_already_submitted: item=%s state=%s",
                item_id, state,
            )
            continue
        if state != _REQUIRED_FROM_STATE:
            skipped_state += 1
            _log.info(
                "submit_to_carrier_skip_state: item=%s state=%s (expected %s)",
                item_id, state, _REQUIRED_FROM_STATE,
            )
            continue
        if no_upload or not target_folder:
            skipped_upload += 1
            _log.info(
                "submit_to_carrier_skip_no_upload: item=%s no_customer_upload=%s "
                "target_folder=%r",
                item_id, no_upload, target_folder,
            )
            continue

        # -- Files: one per revision family, at its current version -----------
        # UPLOAD-VIEW-1 (2026-08-30): was a raw walk of classified
        # associations, which uploaded EVERY revision of a resent document
        # (same filename, N times) and always shipped the as-received bytes,
        # so a TPM's browser edit never reached the carrier. The resolver
        # collapses each revision family to one winner and resolves it to the
        # view-tree file, which IS the current version. Waivers and archive
        # containers are filtered inside the resolver.
        own_files = _list_upload_files(deps, item_id)

        # DRRP1-1 chunk 3 (2026-09-01): documents collected against ANOTHER
        # milestone's work-item that this item is responsible for submitting.
        # For MMK, deliverables received during DRR are submitted as part of P1;
        # every mapped DRR item is no_customer_upload=true, so this is the ONLY
        # route those documents take to the carrier.
        #
        # They upload under THIS item's target_folder, and the source item is
        # left completely alone -- no state transition, no SubmittedToCustomer.
        migrated_files = _list_migrated_files(deps, item_id)
        assocs = _merge_upload_sets(own_files, migrated_files, item_id)
        if migrated_files:
            items_with_migrated += 1

        if not assocs:
            skipped_no_files += 1
            _log.info(
                "submit_to_carrier_skip_no_files: item=%s (no own or migrated files)",
                item_id,
            )
            _audit(deps, "submit_to_carrier_no_files", item_id, {
                "customer_id":    customer_id,
                "milestone_id":   milestone_id,
                "correlation_id": correlation_id,
            })
            continue

        # -- Per-item upload loop ---------------------------------------------
        all_ok       = True
        files_ok     = 0
        files_failed = 0
        device_id    = getattr(item, "device_id", None) or event_context.get("device_id") or ""
        customer_delivery_info = getattr(item, "customer_delivery_info", None) or ""

        for assoc in assocs:
            local_path = getattr(assoc, "relative_path", "") or ""
            # DRRP1-1: non-empty when this file belongs to another milestone's
            # work-item. Carried into every log line and audit row so a carrier
            # folder holding a borrowed document is traceable to its source.
            mig_milestone = getattr(assoc, "migrated_from_milestone", "") or ""
            mig_item_no = int(getattr(assoc, "migrated_from_item_no", 0) or 0)
            is_migrated = bool(mig_milestone)
            source_label = f"{mig_milestone}#{mig_item_no}" if is_migrated else ""

            source_dir, filename = _resolve_source_dir_and_filename(nsd_prefix, local_path)
            if not filename:
                _log.warning(
                    "submit_to_carrier_skip_bad_path: item=%s relative_path=%r "
                    "migrated_from=%s",
                    item_id, local_path[:200], source_label or "-",
                )
                files_failed += 1
                if is_migrated:
                    files_failed_migrated += 1
                all_ok = False
                continue

            # Sub-folder structure under the carrier target folder.
            #
            # UPLOAD-VIEW-1 (2026-08-30): for view-tree files the folder
            # structure is explicit in the path, because the view writer
            # materialised it as real directories under the TG root.
            #
            # UPLOAD-FLAT-1 (2026-08-31): but only ARCHIVE-derived folders are
            # carrier structure. NSD ingest passes the share-relative path as
            # the filename, so an ordinary file sitting in an NSD folder (e.g.
            # `2. DMDform (Done)/report.xlsx`) also carries segments -- and
            # recreating those on the carrier is wrong: a standalone file
            # uploads FLAT at target_folder. The path cannot distinguish the
            # two cases on its own (NEST-1 only prefixes the archive name at
            # depth >= 1, so a top-level zip's entries look identical to NSD
            # folders), so we gate on document_index.from_zip, written at
            # ingest by the archive path only.
            #
            # UPLOAD-SUBDIR-PLM-1 (2026-08-27) still governs the internal-tree
            # FALLBACK, unchanged: derive the subdir from the internal path and
            # keep it PLM-only, so items with no view-tree presence behave
            # exactly as they did before this change.
            effective_target_dir = target_folder
            try:
                is_view = bool(getattr(assoc, "is_view", False))
                # UPLOAD-NSD-SUBDIR-1 (2026-09-09): ingest_source is now
                # consulted on the VIEW branch too, to pick the NSD-specific
                # carrier-relative subdir helper. Previously only fetched for
                # non-view (PLM internal-tree) files.
                ingest_src = ""
                file_hash = getattr(assoc, "file_hash", "") or ""
                if file_hash:
                    doc_ix = deps.storage.get_document_index_row_by_hash(file_hash)
                    ingest_src = (
                        getattr(doc_ix, "ingest_source", "") if doc_ix else ""
                    )
                # UPLOAD-PLAN-1 (2026-09-06): the subdir rule now lives in
                # storage.upload_plan so the TG view and the download-all
                # preview compute the identical destination.
                # UPLOAD-NSD-SUBDIR-1 (2026-09-09): pass the customer's
                # carrier-allowlist tuple and this item's item_description so
                # the NSD branch can strip only the immediate-post-carrier
                # segment that the router actually matched on.
                from core.src.storage.nsd2_resolver import (
                    allowed_root_folders as _allowed_root_folders,
                )
                _carrier_allowed = _allowed_root_folders(customer_id) or ()
                subdir = _carrier_subdir(
                    relative_path=local_path,
                    is_view=is_view,
                    from_zip=bool(getattr(assoc, "from_zip", False)),
                    ingest_source=ingest_src,
                    allowed_carrier_folders=_carrier_allowed,
                    item_description=getattr(item, "item_description", None),
                )
                # UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06): a TPM-chosen folder
                # REPLACES the item's target_folder. The subdir still rides
                # underneath, so archive structure survives a redirect.
                override = (getattr(assoc, "target_folder_override", "") or "").strip()
                if override:
                    _log.warning(
                        "submit_to_carrier: folder override in effect item=%s "
                        "file=%s %r -> %r",
                        item_id, filename, target_folder, override,
                    )
                effective_target_dir = _effective_target_dir(
                    override or target_folder, subdir,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "submit_to_carrier: subdir compute failed item=%s: %s: %s -- "
                    "falling back to flat target",
                    item_id, type(exc).__name__, str(exc)[:120],
                )
                effective_target_dir = target_folder

            try:
                result = _upload_one(
                    deps,
                    device_id=device_id,
                    milestone_id=milestone_id,
                    source_dir=source_dir,
                    target_dir=effective_target_dir,
                    filename=filename,
                    customer_delivery_info=customer_delivery_info,
                )
            except Exception as exc:  # noqa: BLE001
                # Infra failure -- abort task per architect lock. Celery retry
                # will pick up remaining items on the next attempt (already-
                # submitted items skip on re-run via delivery_state check).
                _log.warning(
                    "submit_to_carrier_upload_raised: item=%s file=%s exc=%s",
                    item_id, filename, type(exc).__name__,
                )
                _audit(deps, "submit_to_carrier_upload_raised", item_id, {
                    "customer_id":    customer_id,
                    "milestone_id":   milestone_id,
                    "filename":       filename[:120],
                    "error":          type(exc).__name__,
                    "attempt":        self.request.retries + 1,
                    "correlation_id": correlation_id,
                })
                raise  # Celery autoretry_for catches; final failure -> MaxRetriesExceededError

            ok = bool(getattr(result, "success", False))
            error_code = getattr(result, "error_code", None)
            # DRRP1-1: migrated files get their own action_type so the audit
            # trail shows plainly that a P1 folder received a DRR document,
            # and both carry migrated_from for provenance.
            _mig_details = (
                {"migrated_from_milestone": mig_milestone,
                 "migrated_from_item_no": mig_item_no}
                if is_migrated else {}
            )
            if ok:
                files_ok += 1
                if is_migrated:
                    files_uploaded_migrated += 1
                _audit(
                    deps,
                    "submit_to_carrier_migrated_file_ok" if is_migrated
                    else "submit_to_carrier_file_ok",
                    item_id,
                    {
                        "customer_id":    customer_id,
                        "milestone_id":   milestone_id,
                        "filename":       filename[:120],
                        "target_dir":     effective_target_dir[:120],
                        "correlation_id": correlation_id,
                        **_mig_details,
                    },
                )
            else:
                files_failed += 1
                if is_migrated:
                    files_failed_migrated += 1
                all_ok = False
                _audit(deps, "submit_to_carrier_file_post_verify_failed", item_id, {
                    "customer_id":    customer_id,
                    "milestone_id":   milestone_id,
                    "filename":       filename[:120],
                    "target_dir":     effective_target_dir[:120],
                    "error_code":     error_code or "",
                    "correlation_id": correlation_id,
                    **_mig_details,
                })

        # SUBMIT-STATS-1: accumulate per-file counters into milestone totals
        # BEFORE state-transition branch so partial items still contribute.
        files_uploaded_total += files_ok
        files_failed_total   += files_failed

        # -- Per-item state transition on all-files-success -------------------
        if all_ok and files_ok > 0:
            transitioned = _transition_to_submitted(
                deps,
                item_id=item_id,
                customer_id=customer_id,
                milestone_id=milestone_id,
                correlation_id=correlation_id,
            )
            if transitioned:
                uploaded_items += 1
            else:
                # Files uploaded but state didn't transition -- count as
                # partial so the caller sees the truth. Item stays in RFS;
                # next Submit click will retry the transition (uploads are
                # idempotent per your binding contract).
                partial_items += 1
        else:
            partial_items += 1
            _log.info(
                "submit_to_carrier_partial: item=%s files_ok=%d files_failed=%d "
                "(item stays in %s; will retry on next Submit click)",
                item_id, files_ok, files_failed, _REQUIRED_FROM_STATE,
            )

    _log.info(
        "submit_to_carrier: milestone=%s scanned=%d uploaded_items=%d "
        "partial_items=%d skipped_already=%d skipped_state=%d "
        "skipped_upload=%d skipped_no_files=%d files_uploaded=%d "
        "files_failed=%d migrated_uploaded=%d migrated_failed=%d "
        "items_with_migrated=%d",
        milestone_id, scanned, uploaded_items, partial_items,
        skipped_already, skipped_state, skipped_upload, skipped_no_files,
        files_uploaded_total, files_failed_total,
        files_uploaded_migrated, files_failed_migrated, items_with_migrated,
    )
    return {
        "outcome":            "fired",
        "milestone_id":       milestone_id,
        "customer_id":        customer_id,
        "items_scanned":      scanned,
        "uploaded_items":     uploaded_items,
        "partial_items":      partial_items,
        "skipped_already":    skipped_already,
        "skipped_state":      skipped_state,
        "skipped_upload":     skipped_upload,
        "skipped_no_files":   skipped_no_files,
        # SUBMIT-STATS-1 (2026-08-28): per-file totals so the caller /
        # operator can distinguish "5 work items with 12 files total" from
        # "5 work items with 5 files total". uploaded_items counts items
        # whose ALL files posted OK and whose state advanced; files_uploaded
        # counts every individual file that hit the carrier (including
        # files inside partial_items).
        "files_uploaded":     files_uploaded_total,
        "files_failed":       files_failed_total,
        # DRRP1-1 chunk 3 (2026-09-01): the migrated subset of the above, so a
        # broken mapping reads as "migrated_uploaded dropped to 0" rather than
        # hiding inside a slightly smaller files_uploaded. Both are INCLUDED in
        # files_uploaded / files_failed -- these are a breakdown, not an addend.
        "files_uploaded_migrated": files_uploaded_migrated,
        "files_failed_migrated":   files_failed_migrated,
        "items_with_migrated":     items_with_migrated,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_nsd_volume_prefix(deps: Any) -> str:
    """Look up nsd_volume_prefix from CustomerAdapterConfig. Falls back to
    HILDA_CUSTOMER_ADAPTER_NSD_VOLUME_PREFIX env var, then empty string
    (implies local_nsd_path is already absolute or task is running in the
    same container as the adapter).
    """
    cfg = getattr(deps, "customer_adapter_config", None)
    if cfg is not None:
        prefix = getattr(cfg, "nsd_volume_prefix", "") or ""
        if prefix:
            return os.path.expanduser(prefix)
    return os.path.expanduser(
        os.environ.get("HILDA_CUSTOMER_ADAPTER_NSD_VOLUME_PREFIX", "")
    )


# UPLOAD-PLAN-1 (2026-09-06): the carrier-destination helpers moved to
# core.src.storage.upload_plan so the dashboard can import them without pulling
# Celery into the API container. Re-exported under their original private names
# because tests reference them and the short names read better at the call site.
from core.src.storage.upload_plan import (  # noqa: E402
    ARCHIVE_EXTS as _ARCHIVE_EXTS,
    carrier_subdir as _carrier_subdir,
    effective_target_dir as _effective_target_dir,
    plm_subdir_prefix_from_local_path as _plm_subdir_prefix_from_local_path,
    sanitize_subdir_segment as _sanitize_subdir_segment,
    view_subdir_prefix as _view_subdir_prefix,
)


def _resolve_source_dir_and_filename(
    nsd_prefix: str, local_nsd_path: str,
) -> tuple[str, str]:
    """Compose absolute source_dir + basename filename for the adapter call.

    local_nsd_path from document_item_association is typically stored as a
    relative path rooted at 'internal/...'. Prepend nsd_prefix (host absolute
    mount root) to get the browser-accessible absolute path. If nsd_prefix is
    empty (same-container adapter), the path is used as-is.
    """
    if not local_nsd_path:
        return "", ""
    if nsd_prefix and not os.path.isabs(local_nsd_path):
        full = os.path.join(nsd_prefix, local_nsd_path)
    else:
        full = local_nsd_path
    p = Path(full)
    return str(p.parent), p.name


def _list_upload_files(deps: Any, delivery_item_id: str) -> list[Any]:
    """UPLOAD-VIEW-1 (2026-08-30): resolved upload set for one item -- one file
    per revision family, at its current view-tree version.

    Falls back to the pre-UPLOAD-VIEW-1 raw classified-association walk when
    the resolver isn't wired (older storage impls, hand-built test doubles), so
    nothing that uploads today stops uploading. The fallback keeps the old
    every-revision behaviour; that is the known-imperfect path, not the target.
    """
    resolver = getattr(deps.storage, "list_upload_files_for_item", None)
    if resolver is not None:
        try:
            return list(resolver(delivery_item_id) or [])
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "submit_to_carrier: list_upload_files_for_item failed for "
                "item=%s: %s -- falling back to raw associations",
                delivery_item_id, type(exc).__name__,
            )
    # Fallback shim: adapt DocumentItemAssociation rows to the ItemUploadFile
    # surface the caller reads (relative_path / is_view / file_hash).
    from pathlib import PurePosixPath as _P

    class _AssocAsUploadFile:
        __slots__ = ("relative_path", "filename", "file_hash", "is_view", "doc_type")

        def __init__(self, assoc: Any) -> None:
            self.relative_path = getattr(assoc, "local_nsd_path", "") or ""
            self.filename = _P(self.relative_path).name
            self.file_hash = getattr(assoc, "file_hash", "") or ""
            self.is_view = False
            self.doc_type = ""

    return [_AssocAsUploadFile(a) for a in _list_classified(deps, delivery_item_id)]


def _list_migrated_files(deps: Any, delivery_item_id: str) -> list[Any]:
    """DRRP1-1 chunk 3 (2026-09-01): files another milestone's work-item
    contributes to this item's submission, per milestone_item_mapping.

    Absent resolver (older storage impl, hand-built test double) or any failure
    yields [] -- migration must never break a submission that would otherwise
    succeed with the item's OWN documents. Logged as a warning when the
    resolver exists but raises, because a silent empty here means mapped
    documents that never reach the carrier.
    """
    resolver = getattr(deps.storage, "list_migrated_upload_files_for_item", None)
    if resolver is None:
        return []
    try:
        return list(resolver(delivery_item_id) or [])
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "DRRP1-1: migrated-file lookup failed for item=%s: %s: %s -- "
            "submitting this item's own documents only",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return []


def _merge_upload_sets(
    own_files: list[Any], migrated_files: list[Any], item_id: str,
) -> list[Any]:
    """Own documents first, then migrated ones, de-duplicated by relative_path.

    Per the user's 2026-09-01 clarification a document received in the target
    milestone always differs in hash from the source milestone's versions, so
    both legitimately ship and NO content-level dedup is wanted. The guard here
    is narrower: the same PATH must not be uploaded twice, which could only
    happen if a mapping pointed an item at itself or two resolvers returned the
    same file. Cheap insurance against a duplicate landing on the carrier.

    Own documents are ordered first so that if a carrier folder ends up with a
    filename collision, the item's own document is the one uploaded first.
    """
    merged: list[Any] = []
    seen: set[str] = set()
    for f in list(own_files) + list(migrated_files):
        key = getattr(f, "relative_path", "") or ""
        if key and key in seen:
            _log.warning(
                "DRRP1-1: duplicate upload path skipped for item=%s path=%r",
                item_id, key[:160],
            )
            continue
        if key:
            seen.add(key)
        merged.append(f)
    return merged


def _list_classified(deps: Any, delivery_item_id: str) -> list[Any]:
    """Fetch classified associations for the item via PostgresStorage sync
    wrapper. Falls back to filtering list_associations_for_item if the
    dedicated helper isn't wired (defensive)."""
    dedicated = getattr(deps.storage, "list_classified_associations_for_item", None)
    if dedicated is not None:
        try:
            return list(dedicated(delivery_item_id) or [])
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "submit_to_carrier: list_classified_associations_for_item failed "
                "for item=%s: %s",
                delivery_item_id, type(exc).__name__,
            )
            return []
    # Fallback: pull all, filter by nsd_path_type
    all_lookup = getattr(deps.storage, "list_associations_for_item", None)
    if all_lookup is None:
        return []
    try:
        rows = list(all_lookup(delivery_item_id) or [])
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "submit_to_carrier: list_associations_for_item failed for item=%s: %s",
            delivery_item_id, type(exc).__name__,
        )
        return []
    def _is_classified(r: Any) -> bool:
        val = getattr(r, "nsd_path_type", None)
        return getattr(val, "value", val) == "classified"
    return [r for r in rows if _is_classified(r)]


def _upload_one(
    deps: Any,
    *,
    device_id: str,
    milestone_id: str,
    source_dir: str,
    target_dir: str,
    filename: str,
    customer_delivery_info: str,
) -> Any:
    """Bridge the async CustomerAdapter.upload_attachment call into the sync
    Celery task body. Uses a fresh event loop per call (matches the pattern
    in submission._run_sync).
    """
    import asyncio
    coro = deps.customer_adapter.upload_attachment(
        device_id=device_id,
        milestone_name=milestone_id,
        source_dir=Path(source_dir),
        target_dir=target_dir,
        filename=filename,
        customer_delivery_info=customer_delivery_info,
    )
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


def _transition_to_submitted(
    deps: Any,
    *,
    item_id: str,
    customer_id: str,
    milestone_id: str,
    correlation_id: str,
) -> bool:
    """Transition item RFS -> SubmittedToCustomer via tracker.transitions.
    Returns True on successful transition, False on failure so the caller can
    avoid falsely counting a failed transition as an upload success.

    Lazy import: workflow_engine.tasks transitively imports tracker in other
    flows; keeping this local avoids circular-import friction (matches the
    kickoff_collection_task pattern).

    Guard 4 (guards.py) trusts trigger_source='submit_to_carrier_task' as
    authoritative evidence of upload completion, so we don't need to write
    a carrier_upload_complete flag first (the flag isn't a column on
    DeliveryItemTable in this codebase; prior write attempts silently
    no-op'd via update_delivery_item's hasattr check)."""
    from core.src.tracker import DeliveryState
    from core.src.tracker.transitions import update_delivery_state

    try:
        transition_result = update_delivery_state(
            delivery_item_id=item_id,
            target_state=DeliveryState.SUBMITTED_TO_CUSTOMER,
            params={},
            event_context={
                "correlation_id":   correlation_id,
                "customer_id":      customer_id,
                "milestone_id":     milestone_id,
                "delivery_item_id": item_id,
                "trigger_source":   "submit_to_carrier_task",
            },
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
            bypass_guards=False,
        )
    except Exception as exc:  # noqa: BLE001
        # State transition failure is loud but non-fatal for the outer
        # iteration -- next Submit click / retry can re-run.
        _log.warning(
            "submit_to_carrier: state transition raised for item=%s: %s",
            item_id, type(exc).__name__,
        )
        _audit(deps, "submit_to_carrier_transition_failed", item_id, {
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "error":          type(exc).__name__,
            "correlation_id": correlation_id,
        })
        return False
    # update_delivery_state can return without raising when the transition is
    # guard_denied. Inspect the TransitionResult.outcome so we don't falsely
    # count a blocked transition as uploaded_items++ in the caller.
    outcome = getattr(transition_result, "outcome", None)
    if outcome not in ("transitioned", "no_op_idempotent"):
        _log.warning(
            "submit_to_carrier: state transition non-success for item=%s "
            "outcome=%s",
            item_id, outcome,
        )
        _audit(deps, "submit_to_carrier_transition_denied", item_id, {
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "outcome":        str(outcome),
            "correlation_id": correlation_id,
        })
        return False
    return True


def _audit(
    deps: Any,
    action_type: str,
    delivery_item_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit writer -- failures never break the outer flow."""
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": details.get("trigger_source", "automated"),
                "correlation_id": details.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("submit_to_carrier: audit write failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

register_task_binding(TaskBinding(
    action_kind=ActionKind.SUBMIT_TO_CARRIER,
    celery_task=submit_to_carrier_task,
    # Same queue as other adapter-touching tasks per MODULE.md queue topology
    # (browser_automation -- 10-100x slower than REST per [D-054]; 1 worker
    # per host per session pool). Selecting this queue keeps parallelism
    # correct for the selenium session pool.
    queue="browser_automation",
))
