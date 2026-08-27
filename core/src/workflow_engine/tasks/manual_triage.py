"""MTR-1 (2026-08-27) -- refresh manual_triage_required flag after view-tree save.

Called after every save_view_document invocation (TPM edit via OnlyOffice, owner
resend via ingest). Recomputes needs_merge across the TG's files, aggregates per
delivery_item, updates Postgres, best-effort SP writeback.

Design intent (user 2026-08-27):
  * When a doc has a mix of TPM-authored and owner-authored versions AND the
    CURRENT version is owner (auto) -- TPM's earlier edit was overwritten by an
    owner resend -- needs_merge=True (existing MERGE-1 logic).
  * HILDA persists this state as delivery_item.manual_triage_required = True on
    ALL items with any such doc.
  * SP UI's Submit-to-Carrier button disables when ANY item in the milestone has
    manual_triage_required = True.
  * When TPM edits again (their edit is now the current version), needs_merge
    flips False -> HILDA clears the flag -> SP button re-enables.

Pattern A (D-164): HILDA writes state, SP reads state and acts. This module owns
the write path; SP UI owns the read + button-disable enforcement.

manual_triage_required is HILDA-owned at runtime -- NOT in sync_deliverable_fields
_BOOL_FIELDS allowlist, so subsequent SP CHANGED alerts don't clobber HILDA's
runtime writes. Template.yaml value ("No" by default) applies at first row create
only.

Best-effort semantics: failures at any step log a warning + audit but never raise,
so a WOPI save (or an ingest write) never fails because of a triage-flag update
glitch. The next save reprocesses; convergent by design.

Cross-item aggregation: one file_hash CAN in theory associate to multiple items
(shared docs), but per MMK's ["default"] TG design each doc lands on exactly one
item (router semantics + D-153 cross-TG constraint). The helper handles both.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from core.src.storage.db import (
    DocumentItemAssociationTable, DocumentVersionTable, session_scope,
)
from core.src.storage.document_view_ops import list_files_in_tg

__all__ = ["refresh_manual_triage_after_view_save"]

_log = logging.getLogger(__name__)


async def refresh_manual_triage_after_view_save(
    deps: Any,
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    tg_name: str,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Recompute + persist manual_triage_required for every delivery_item in the
    TG whose value differs from the freshly-computed aggregate.

    Returns telemetry: items_recomputed / items_flipped / sp_writeback_ok /
    sp_writeback_failed.

    Never raises -- best-effort. Every failure is logged + audited so ops has a
    trail without breaking the caller (WOPI save / inbound ingest).
    """
    stats = {
        "items_recomputed":     0,
        "items_flipped":        0,
        "sp_writeback_ok":      0,
        "sp_writeback_failed":  0,
    }
    try:
        files = await list_files_in_tg(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, tg_name=tg_name,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "MTR-1: list_files_in_tg failed customer=%s device=%s milestone=%s tg=%r: %s: %s",
            customer_id, device_id, milestone_id, tg_name,
            type(exc).__name__, str(exc)[:200],
        )
        return stats

    if not files:
        return stats

    # Build view_relative_path -> needs_merge map. needs_merge is a per-file
    # (per-path) property computed over ALL versions' saved_by history in
    # list_files_in_tg, so it's the same for every version of a given path.
    path_needs_merge: dict[str, bool] = {}
    for f in files:
        vrp = getattr(f, "view_relative_path", "") or ""
        if vrp:
            path_needs_merge[vrp] = bool(getattr(f, "needs_merge", False))

    if not path_needs_merge:
        return stats

    # Resolve items via document_item_association. The assoc is keyed on the
    # V1 (ingest-time) sha256, NOT the current version's sha256 -- TPM edits
    # + owner resends insert new document_version rows but never create new
    # associations. So we look up ALL sha256s per view path and match assocs
    # against ANY of them (v1's hash will be in that set).
    try:
        async with session_scope() as session:
            version_rows = (await session.execute(
                select(
                    DocumentVersionTable.view_relative_path,
                    DocumentVersionTable.sha256,
                ).where(
                    DocumentVersionTable.view_relative_path.in_(
                        list(path_needs_merge.keys())
                    )
                )
            )).all()
            if not version_rows:
                return stats
            # Build sha256 -> view_relative_path (for lookup after assoc query).
            sha_to_path: dict[str, str] = {}
            all_shas: set[str] = set()
            for vrp, sha in version_rows:
                if sha:
                    sha_to_path[sha] = vrp
                    all_shas.add(sha)
            if not all_shas:
                return stats
            assoc_rows = (await session.execute(
                select(
                    DocumentItemAssociationTable.file_hash,
                    DocumentItemAssociationTable.delivery_item_id,
                ).where(
                    DocumentItemAssociationTable.file_hash.in_(list(all_shas))
                )
            )).all()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "MTR-1: version/assoc lookup failed tg=%r: %s: %s",
            tg_name, type(exc).__name__, str(exc)[:200],
        )
        return stats

    # Aggregate needs_merge per item: OR across all files (view paths) whose
    # assoc's file_hash resolves back to that path.
    item_to_needs_merge: dict[str, bool] = {}
    for file_hash, item_id in assoc_rows:
        if not item_id:
            continue
        vrp = sha_to_path.get(file_hash, "")
        if not vrp:
            continue
        needs = path_needs_merge.get(vrp, False)
        item_to_needs_merge[item_id] = (
            item_to_needs_merge.get(item_id, False) or needs
        )

    if not item_to_needs_merge:
        return stats

    # For each affected item: read current Postgres value, compare, update if
    # different, best-effort SP writeback + audit.
    for item_id, target_flag in item_to_needs_merge.items():
        stats["items_recomputed"] += 1
        try:
            item = deps.storage.get_delivery_item(item_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "MTR-1: get_delivery_item failed item=%s: %s: %s",
                item_id, type(exc).__name__, str(exc)[:120],
            )
            continue
        if item is None:
            continue

        current_flag = bool(getattr(item, "manual_triage_required", False))
        if current_flag == target_flag:
            continue

        # Postgres update
        try:
            deps.storage.update_delivery_item(
                item_id, {"manual_triage_required": target_flag},
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "MTR-1: postgres update failed item=%s: %s: %s",
                item_id, type(exc).__name__, str(exc)[:120],
            )
            continue

        stats["items_flipped"] += 1
        action_type = (
            "manual_triage_required_set" if target_flag
            else "manual_triage_required_cleared"
        )

        # SP writeback (best-effort)
        sp_written = _writeback_to_sp(deps, item, target_flag)
        if sp_written:
            stats["sp_writeback_ok"] += 1
        else:
            stats["sp_writeback_failed"] += 1

        # Audit
        _audit(deps, action_type, item_id, {
            "customer_id":     customer_id,
            "device_id":       device_id,
            "milestone_id":    milestone_id,
            "tg_name":         tg_name,
            "correlation_id":  correlation_id,
            "prior":           current_flag,
            "new":             target_flag,
            "sp_written":      sp_written,
        })

    _log.info(
        "MTR-1: tg=%r customer=%s device=%s milestone=%s stats=%s",
        tg_name, customer_id, device_id, milestone_id, stats,
    )
    return stats


def _writeback_to_sp(deps: Any, item: Any, target_flag: bool) -> bool:
    """Best-effort SP writeback via sp_writer.update_item. Prefers item.sp_id
    (set at import per D-092); falls back to natural-key lookup. Returns True on
    success, False on any failure (logged, never raised)."""
    sp_writer = getattr(deps, "sp_writer", None)
    if sp_writer is None:
        return False
    customer_id = getattr(item, "customer_id", None)
    if not customer_id:
        return False
    try:
        from core.src.sharepoint_integration.config import ListScope
        scope = ListScope(customer_id=customer_id)

        # Prefer stored sp_id (avoids a get_items round-trip).
        sp_id_val = getattr(item, "sp_id", None)
        if sp_id_val:
            sp_writer.update_item(
                entity="delivery_items",
                scope=scope,
                item_id=str(sp_id_val),
                canonical_fields={"manual_triage_required": target_flag},
            )
            return True

        # Natural-key fallback (mirrors owner_reply.py pattern).
        item_no = getattr(item, "item_no", None)
        milestone_id = getattr(item, "milestone_id", None)
        device_id = (
            getattr(item, "device_id", None)
            or getattr(item, "project_model", None)
        )
        filters: dict[str, Any] = {}
        if item_no is not None:
            filters["item_no"] = item_no
        if milestone_id:
            filters["milestone_id"] = milestone_id
        if device_id:
            filters["project_model"] = device_id
        if not filters:
            return False
        rows = sp_writer.get_items(
            entity="delivery_items", scope=scope, canonical_filters=filters,
        ) or []
        if not rows:
            return False
        sp_row_id = rows[0].get("_sp_id")
        if not sp_row_id:
            return False
        sp_writer.update_item(
            entity="delivery_items",
            scope=scope,
            item_id=str(sp_row_id),
            canonical_fields={"manual_triage_required": target_flag},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "MTR-1: SP writeback failed item_id=%s: %s: %s",
            getattr(item, "item_id", None),
            type(exc).__name__, str(exc)[:200],
        )
        return False


def _audit(deps: Any, action_type: str, item_id: str, details: dict[str, Any]) -> None:
    """Best-effort audit -- mirror the pattern in reconcile._audit."""
    audit = getattr(deps, "audit", None)
    if audit is None:
        return
    write = getattr(audit, "write", None) or getattr(audit, "log", None)
    if write is None:
        return
    try:
        write(action_type=action_type, delivery_item_id=item_id, details=details)
    except Exception:  # noqa: BLE001
        pass
