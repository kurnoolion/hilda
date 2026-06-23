"""FR-83 TPM-manual document reassignment from Default work-item to a
template-defined work-item.

Called by workflow_engine REASSIGN_DOCUMENT_TO_WORK_ITEM task body when
`sp_alert_parser` dispatches the `tpm_reassign_to_workitem` action verb
from the FR-87 step (A) button click on the HILDA-rendered document section.

Pipeline (Ph-1):
1. Verify source is a Default work-item -- TRK-E002 otherwise.
2. Verify target item_type aligns with doc_type per FR-86 matrix --
   TRK-E003 otherwise.
3. Update DocumentItemAssociation: replace source_item_id -> target_item_id.
4. [D-039] Step 0 (hash dedup): duplicate_skipped if file_hash already
   exists on target.
5. [D-039] Step 1 (slug match): exact match -> revision_of; no entries ->
   new_document; multiple non-exact -> Ph-1 falls back to new_document with
   TRK-W007 (Ph-2 will add Step 2 LLM CLASSIFY_DOC + Step 3 staged per
   architect direction 2026-06-21 CLASSIFY_DOC Ph-2 demotion).
6. Re-derive doc_type per FR-85 ladder if the inferred doc_type differs
   from what the source default WI had.
7. Re-run FR-77 Type-2 routing for the document -> resolves new target_folder.
8. Enqueue customer_adapter.upload_attachment if applicable.
9. Audit + SP writeback.

Ph-1/Ph-2 boundaries:
- Step 2 LLM disambiguation deferred per CLASSIFY_DOC Ph-2 demotion.
  Ph-1 emits TRK-W007 and falls through to new_document(rev1); Ph-2 may
  revise revision_classification after-the-fact.
- Step 3 staged area (_staged_revision/) is Ph-2 only per FR-86; Ph-1
  has only rev1 (single-revision flow per FR-7).
- customer_adapter.upload_attachment enqueue is a stub here in tracker
  (returns the would-be task spec); concrete dispatch happens at the
  workflow_engine task-body layer once customer_adapter is impl'd.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from core.src.diagnostics.error_codes import PipelineError
from core.src.template_schema import DocType, ItemType

from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter

__all__ = [
    "ReassignmentResult",
    "RevisionClassification",
    "reassign_document_to_workitem",
]


RevisionClassification = Literal["new_document", "revision_of", "duplicate_skipped"]


@dataclass(frozen=True)
class ReassignmentResult:
    file_hash: str
    source_item_id: str
    target_item_id: str
    rederived_doc_type: DocType
    revision_classification: RevisionClassification
    revision_of_doc_id_slug: str | None
    rev_number: int
    fr77_re_routed: bool
    upload_dispatched: bool
    correlation_id: str


# ---------------------------------------------------------------------------
# FR-86 alignment matrix
# ---------------------------------------------------------------------------


def _doc_type_aligns_with_item_type(doc_type: DocType, item_type_value: str) -> bool:
    """FR-86 storage matrix alignment check.

    - TEST_TECH_WAIVER_REPORT  <-> {test_report, tech_report, waiver}
    - COMPLIANCE_CERTIFICATION_RELEASE_NOTES <-> compliance_certification_release_notes
    - DEFAULT                  <-> any (default WI accepts everything)
    - CONFIRMATION             <-> none (no docs expected)
    - unresolved doc_type      <-> any (caller-side sentinel; matrix bypassed)
    """
    if doc_type == DocType.UNRESOLVED:
        return True

    if item_type_value == ItemType.DEFAULT.value:
        return True

    if item_type_value == ItemType.CONFIRMATION.value:
        return False

    if item_type_value == ItemType.TEST_TECH_WAIVER_REPORT.value:
        return doc_type in (DocType.TEST_REPORT, DocType.TECH_REPORT, DocType.WAIVER)

    if item_type_value == ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value:
        return doc_type == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES

    # Unknown item_type (template registry extension); reject conservatively.
    return False


def _is_default_workitem(item) -> bool:
    return getattr(item, "item_type", None) == ItemType.DEFAULT.value


# ---------------------------------------------------------------------------
# [D-039] Steps 0-1 (Ph-1 deterministic)
# ---------------------------------------------------------------------------


def _slugify(filename: str) -> str:
    """FR-83 + [D-039] Step 1 slug derivation. Filesystem-safe alphanumeric
    + - + _ from filename minus extension. Ph-1 simple implementation; Ph-2
    may add unicode normalization + customer-specific stop-word filters.
    """
    name, _ = os.path.splitext(filename or "")
    out = []
    for ch in name.lower():
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        elif ch in (" ", ".", "/"):
            out.append("_")
    return "".join(out).strip("_") or "untitled"


def _check_d039_steps_0_1(
    storage: StorageWriter,
    target_item_id: str,
    target_doc_type: DocType,
    file_hash: str,
    original_filename: str,
) -> tuple[RevisionClassification, str | None, int, list[str]]:
    """Returns (classification, revision_of_doc_id_slug, rev_number, warnings).

    Ph-1: Steps 0 (hash dedup) + 1 (slug match). Step 2 LLM + Step 3 staged
    are Ph-2 per architect direction 2026-06-21.

    Concrete impl reads via two StorageWriter calls (find existing rows by
    (target_item_id, doc_type)); tracker's Protocol surface doesn't expose
    these directly -- caller (workflow_engine task body) wires them via
    `find_existing_doc_rows` helper on the concrete storage class. For
    Ph-1 we expose them via getattr on the storage Protocol -- structural
    typing means concrete `storage` only needs to implement them.
    """
    warnings: list[str] = []

    # Step 0: hash dedup
    hash_check = getattr(storage, "find_doc_index_row_by_hash", None)
    if hash_check is not None:
        existing = hash_check(file_hash=file_hash, target_item_id=target_item_id,
                              doc_type=target_doc_type)
        if existing is not None:
            return (
                "duplicate_skipped",
                getattr(existing, "doc_id_slug", None),
                getattr(existing, "rev_number", 1),
                warnings,
            )

    # Step 1: slug match
    candidate_slug = _slugify(original_filename)
    list_existing = getattr(storage, "list_doc_id_slugs_for_item_doc_type", None)
    existing_slugs: list[tuple[str, int]] = []
    if list_existing is not None:
        existing_slugs = list_existing(target_item_id=target_item_id,
                                       doc_type=target_doc_type)

    if not existing_slugs:
        return ("new_document", None, 1, warnings)

    exact_matches = [(s, r) for s, r in existing_slugs if s == candidate_slug]
    if exact_matches:
        max_rev = max(r for _, r in exact_matches)
        return ("revision_of", candidate_slug, max_rev + 1, warnings)

    # Multiple non-exact matches: Ph-1 falls through to new_document; emit TRK-W007
    # (Ph-2 LLM disambiguation deferred). The candidate_slug list is shown in the
    # warning for ops triage / Ph-2 LLM revisit.
    if len(existing_slugs) > 1:
        warnings.append(
            f"TRK-W007:candidates={','.join(s for s, _ in existing_slugs[:5])}"
        )
    return ("new_document", None, 1, warnings)


# ---------------------------------------------------------------------------
# FR-85 doc_type re-derivation (Ph-1 placeholder; concrete impl in email_service)
# ---------------------------------------------------------------------------


def _rederive_doc_type(
    storage: StorageWriter,
    target_item_id: str,
    file_hash: str,
    current_doc_type: DocType,
) -> DocType:
    """Re-derive doc_type via FR-85 ladder on the new item context.

    Ph-1: Step 1 filename regex is the only active classifier (architect
    direction 2026-06-22 CLASSIFY_DOC_TYPE demotion). Concrete classifier
    lives in `email_service.doc_type_classifier`; tracker calls it via the
    storage Protocol's optional `rederive_doc_type_for_file` helper if
    present, else returns the current_doc_type unchanged.

    Ph-2 will integrate FR-85 Step 2 LLM CLASSIFY_DOC_TYPE for low-confidence
    Step 1 cases.
    """
    classifier = getattr(storage, "rederive_doc_type_for_file", None)
    if classifier is None:
        return current_doc_type
    result = classifier(file_hash=file_hash, target_item_id=target_item_id)
    return result if isinstance(result, DocType) else current_doc_type


# ---------------------------------------------------------------------------
# FR-77 Type-2 routing (Ph-1 placeholder)
# ---------------------------------------------------------------------------


def _rerun_fr77_routing(
    storage: StorageWriter,
    target_item_id: str,
    file_hash: str,
) -> tuple[bool, str | None]:
    """Re-run FR-77 Type-2 carrier-portal target_folder resolution.

    Ph-1 calls the storage Protocol's optional `resolve_target_folder` helper.
    Returns (re_routed: bool, target_folder: str | None).
    """
    resolver = getattr(storage, "resolve_target_folder", None)
    if resolver is None:
        return (False, None)
    folder = resolver(target_item_id=target_item_id, file_hash=file_hash)
    return (folder is not None, folder)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def reassign_document_to_workitem(
    file_hash: str,
    source_item_id: str,
    target_item_id: str,
    pm_id: str,
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any],
    original_filename: str = "",
) -> ReassignmentResult:
    """FR-83 TPM-manual document reassignment via SP UI. See module docstring.

    `original_filename` is required for [D-039] Step 1 slug derivation;
    concrete callers obtain it from the DocumentIndexRow row. Defaulted to
    empty here so the function can be unit-tested without full doc-row
    fixtures; in that case Step 1 generates 'untitled' which still works.
    """
    correlation_id = event_context.get("correlation_id", "")

    source = storage.get_delivery_item(source_item_id)
    if not _is_default_workitem(source):
        raise PipelineError(
            "TRK-E002",
            context={"source_item_id": source_item_id},
        )

    target = storage.get_delivery_item(target_item_id)
    target_item_type = getattr(target, "item_type", None)

    # Discover the document's current doc_type via storage Protocol optional helper.
    doc_index_row_lookup = getattr(storage, "get_doc_index_row", None)
    current_doc_type = DocType.UNRESOLVED
    if doc_index_row_lookup is not None:
        existing = doc_index_row_lookup(file_hash=file_hash)
        if existing is not None:
            doc_type_val = getattr(existing, "doc_type", None)
            if isinstance(doc_type_val, DocType):
                current_doc_type = doc_type_val
            elif isinstance(doc_type_val, str):
                try:
                    current_doc_type = DocType(doc_type_val)
                except ValueError:
                    current_doc_type = DocType.UNRESOLVED

    # FR-86 alignment check against the CURRENT doc_type (pre-re-derivation).
    if not _doc_type_aligns_with_item_type(current_doc_type, target_item_type):
        raise PipelineError(
            "TRK-E003",
            context={
                "target_item_id": target_item_id,
                "target_type": target_item_type,
                "doc_type": current_doc_type.value,
            },
        )

    # Step 3: update the DocumentItemAssociation -- replace source with target,
    # clear unrouted_source_path on the row.
    storage.update_document_item_association(
        file_hash=file_hash,
        fields={
            "delivery_item_id": target_item_id,
            "unrouted_source_path": None,
        },
    )

    # Steps 4-5: [D-039] Steps 0-1.
    classification, revision_of_slug, rev_number, w_step01 = _check_d039_steps_0_1(
        storage=storage,
        target_item_id=target_item_id,
        target_doc_type=current_doc_type,
        file_hash=file_hash,
        original_filename=original_filename,
    )
    warnings = list(w_step01)

    # Step 6: re-derive doc_type (FR-85) -- may differ from default-WI assignment.
    rederived = _rederive_doc_type(storage, target_item_id, file_hash, current_doc_type)
    if rederived != current_doc_type:
        # If re-derivation changed doc_type, re-check FR-86 alignment + re-run Steps 0-1
        # against the new doc_type scope. Update the DocumentIndexRow doc_type field.
        if not _doc_type_aligns_with_item_type(rederived, target_item_type):
            raise PipelineError(
                "TRK-E003",
                context={
                    "target_item_id": target_item_id,
                    "target_type": target_item_type,
                    "doc_type": rederived.value,
                },
            )
        storage.update_document_index_row(
            file_hash=file_hash,
            fields={"doc_type": rederived.value},
        )
        # Re-run Steps 0-1 in the new doc_type scope.
        classification, revision_of_slug, rev_number, w_step01_redo = _check_d039_steps_0_1(
            storage=storage,
            target_item_id=target_item_id,
            target_doc_type=rederived,
            file_hash=file_hash,
            original_filename=original_filename,
        )
        warnings.extend(w_step01_redo)

    final_doc_type = rederived

    # Step 7: re-run FR-77 Type-2 routing.
    fr77_re_routed, target_folder = _rerun_fr77_routing(storage, target_item_id, file_hash)

    # Step 8: enqueue carrier upload if applicable.
    upload_dispatched = False
    no_customer_upload = bool(getattr(target, "no_customer_upload", False))
    if (
        classification != "duplicate_skipped"
        and not no_customer_upload
        and target_folder is not None
    ):
        # Enqueue stub: concrete dispatch happens at workflow_engine task-body layer
        # once customer_adapter is implemented. Storage Protocol optional helper.
        enqueue = getattr(storage, "enqueue_upload_attachment", None)
        if enqueue is not None:
            enqueue(
                file_hash=file_hash,
                delivery_item_id=target_item_id,
                target_folder=target_folder,
                rev_number=rev_number,
            )
            upload_dispatched = True

    # Step 9: audit + SP writeback.
    now = datetime.now(timezone.utc)
    audit.write_communication_log(
        action_type="tpm_reassign_to_workitem",
        delivery_item_id=target_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "tpm_button"),
            "correlation_id": correlation_id,
            "modified_by": pm_id,
        },
        details={
            "file_hash": file_hash,
            "source_item_id": source_item_id,
            "target_item_id": target_item_id,
            "rederived_doc_type": final_doc_type.value,
            "revision_classification": classification,
            "revision_of_doc_id_slug": revision_of_slug,
            "rev_number": rev_number,
            "fr77_re_routed": fr77_re_routed,
            "upload_dispatched": upload_dispatched,
            "warnings": warnings,
            "modified_at": now.isoformat(),
        },
    )

    # Step 10: clear the SP-side tpm_reassignment_target_item_id field on the
    # default WI per Invariant; emit TRK-W005 if it was already null (UX out of sync).
    source_tpm_reassign_field = getattr(source, "tpm_reassignment_target_item_id", None)
    sp_writer.update_item(
        entity="delivery_items",
        scope=event_context.get("sp_scope"),
        item_id=source_item_id,
        canonical_fields={"tpm_reassignment_target_item_id": None},
    )
    if source_tpm_reassign_field is None:
        audit.write_communication_log(
            action_type="tpm_reassign_field_already_clear",
            delivery_item_id=source_item_id,
            attribution={
                "trigger_source": event_context.get("trigger_source", "tpm_button"),
                "correlation_id": correlation_id,
                "modified_by": pm_id,
            },
            details={"code": "TRK-W005"},
        )

    return ReassignmentResult(
        file_hash=file_hash,
        source_item_id=source_item_id,
        target_item_id=target_item_id,
        rederived_doc_type=final_doc_type,
        revision_classification=classification,
        revision_of_doc_id_slug=revision_of_slug,
        rev_number=rev_number,
        fr77_re_routed=fr77_re_routed,
        upload_dispatched=upload_dispatched,
        correlation_id=correlation_id,
    )
