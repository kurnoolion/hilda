"""Fr52AttachmentRouter -- the FR-52 5-step routing pipeline + FR-85 2-step
doc_type classification ladder + FR-86 4-path storage matrix dispatcher.

Per email_service/MODULE.md "Per-attachment pipeline" section + 2026-06-25 cascade.

Ph-1 first cut narrowing (per llm/MODULE.md phasing + MODULE.md test-scenario):
- Branch A Step A1 (filename regex) is ACTIVE
- Branch A Step A2 (LLM CLASSIFY_DOC_TYPE) is Ph-1 NEXT pass -- stub-with-skip:
  if regex doesn't match, doc_type = UNRESOLVED + classification_resolution =
  UNRESOLVED_LOW_CONFIDENCE
- Branch B Steps B1/B2 (substring + fuzzy) are ACTIVE
- Branch B Step B3 (FR-77 folder routing) gated by folder_routing_enabled
  (False in basic flow -> skipped)
- Branch B Step B4 (LLM ROUTE_ATTACHMENT) is ACTIVE; emit EML-W007 over threshold
- Branch B Step B5 (staged-to-default) is ACTIVE
- Step C [D-039] revision determination -- Ph-1 first cut runs Step 1 slug match
  only; Step 2/3 (LLM CLASSIFY_DOC) is Ph-2
- Step F PLM upload + FR-53 review are config-gated (False in test scenario)
"""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

import yaml
from rapidfuzz import fuzz

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.protocol import (
    AttachmentItemMatch,
    AttachmentRouter,
    ClassificationResolution,
    InboundAttachment,
    RoutedAttachment,
)
from core.src.storage.models import NSDPathType, RoutingResolution
from core.src.template_schema.enums import DocType, ItemType

if TYPE_CHECKING:
    from core.src.llm.protocol import LLMProvider

__all__ = ["Fr52AttachmentRouter", "StorageBackend", "TgResolverProtocol", "load_doc_type_rules"]

logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """Minimal storage surface Fr52AttachmentRouter needs.

    Concrete impl: `core.src.storage` module functions (passed in as a thin
    namespace shim or by binding the functions onto a fixture). Tests use
    InMemoryStorage / MockStorage that records calls.
    """

    async def get_document_index_row_by_hash(self, file_hash: str) -> Any: ...
    async def add_document_index_row(self, row: Any) -> None: ...
    async def add_document_item_association(self, assoc: Any) -> None: ...
    async def find_doc_id_slugs_for_item(self, delivery_item_id: str, doc_type: Any) -> list[str]: ...
    async def write_file(self, path: Any, content: Any) -> None: ...
    async def log_communication(self, row: Any) -> None: ...


class TgResolverProtocol(Protocol):
    """Email-channel TG resolver -- a callable wrapping resolve_tg_from_email."""

    def __call__(
        self,
        candidate_items: list[dict],
        sender: str,
        to_addrs: tuple[str, ...],
        cc_addrs: tuple[str, ...],
    ) -> str | None: ...


def load_doc_type_rules(rules_path: Path) -> dict[str, list[re.Pattern[str]]]:
    """Load YAML filename regex rules per FR-85 Step 1.

    YAML shape:
        <doc_type>:
          - regex: '...'
            flags: IGNORECASE
          - regex: '...'

    Returns: {doc_type_value: [compiled_pattern, ...]}.

    Falls back to the universal default at
    core/src/email_service/default_doc_type_rules.yaml when rules_path missing
    or unreadable.
    """
    if not rules_path.is_file():
        # Fallback to universal default rules shipped with the module
        rules_path = Path(__file__).resolve().parent.parent / "default_doc_type_rules.yaml"
    if not rules_path.is_file():
        return {}

    with rules_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    compiled: dict[str, list[re.Pattern[str]]] = {}
    for doc_type, patterns in raw.items():
        compiled_list: list[re.Pattern[str]] = []
        for entry in patterns or []:
            regex = entry.get("regex") if isinstance(entry, dict) else None
            if not regex:
                continue
            flags_str = entry.get("flags", "") if isinstance(entry, dict) else ""
            flags = 0
            if "IGNORECASE" in (flags_str or "").upper():
                flags |= re.IGNORECASE
            compiled_list.append(re.compile(regex, flags))
        if compiled_list:
            compiled[doc_type] = compiled_list
    return compiled


class Fr52AttachmentRouter:
    """Conforms to AttachmentRouter Protocol. See module docstring for pipeline."""

    def __init__(
        self,
        storage: StorageBackend,
        llm: "LLMProvider | None",
        tg_resolver: TgResolverProtocol | None,
        doc_type_filename_rules_path: Path,
        *,
        doc_type_classifier_threshold: float = 0.85,
        route_attachment_max_matches_threshold: int = 10,
        issue_tracker: Any = None,                 # gated by plm_upload_enabled; None in basic flow
        fuzzy_threshold: float = 0.85,
        llm_confidence_threshold: float = 0.75,
        plm_upload_enabled: bool = True,
        review_required_enabled: bool = True,
    ) -> None:
        self._storage = storage
        self._llm = llm
        self._tg_resolver = tg_resolver
        self._rules_path = doc_type_filename_rules_path
        self._doc_type_classifier_threshold = doc_type_classifier_threshold
        self._max_matches_threshold = route_attachment_max_matches_threshold
        self._issue_tracker = issue_tracker
        self._fuzzy_threshold = fuzzy_threshold
        self._llm_confidence_threshold = llm_confidence_threshold
        self._plm_upload_enabled = plm_upload_enabled
        self._review_required_enabled = review_required_enabled
        self._rules_cache: dict[str, list[re.Pattern[str]]] | None = None

    def _rules(self) -> dict[str, list[re.Pattern[str]]]:
        if self._rules_cache is None:
            self._rules_cache = load_doc_type_rules(self._rules_path)
        return self._rules_cache

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def route(
        self,
        attachment: InboundAttachment,
        batch_id: str,
        candidate_items: list[dict],
    ) -> RoutedAttachment:
        """Run the full per-attachment pipeline (Step 0 .. Step F).

        Returns RoutedAttachment with the routing + classification + NSD-path
        decisions; the file write + index-row write + associations are
        performed against the injected storage.
        """
        # ---- Step 0: file_hash dedup ([D-039] Step 0) ---------------------
        existing = await self._storage.get_document_index_row_by_hash(
            attachment.file_hash
        )
        if existing is not None:
            return RoutedAttachment(
                file_hash=attachment.file_hash,
                matches=(),
                doc_type=str(getattr(existing, "doc_type", DocType.UNRESOLVED.value)),
                doc_id_slug=getattr(existing, "doc_id_slug", None),
                rev_number=getattr(existing, "rev_number", None),
                classification_resolution=ClassificationResolution.FILENAME_REGEX,
                routing_resolution=RoutingResolution.SUBSTRING_MATCH,
                inferred_tg_name=getattr(existing, "inferred_tg_name", None),
                nsd_path_type=NSDPathType.CLASSIFIED,
                is_duplicate=True,
            )

        # ---- Branch A: FR-85 doc_type classification ----------------------
        doc_type_value, cls_resolution = self._classify_doc_type(attachment.filename)

        # ---- Branch B: FR-52 item routing ---------------------------------
        matches, routing_resolution = await self._route_to_items(
            attachment, candidate_items
        )

        # FR-79 over-routing warning
        if len(matches) > self._max_matches_threshold:
            logger.warning(
                "EML-W007: %d matches on file_hash=%s exceeds threshold=%d",
                len(matches),
                attachment.file_hash,
                self._max_matches_threshold,
            )

        # ---- inferred_tg_name resolution (email channel only) ------------
        inferred_tg = None
        if self._tg_resolver is not None:
            inferred_tg = self._tg_resolver(
                candidate_items=candidate_items,
                sender="",
                to_addrs=(),
                cc_addrs=(),
            )

        # ---- Step C: [D-039] new-vs-revision determination ----------------
        # Gated on (doc_type != UNRESOLVED AND item_type != Default).
        doc_id_slug: str | None = None
        rev_number: int | None = None
        primary_item = matches[0] if matches else None
        primary_item_dict = None
        if primary_item:
            for cand in candidate_items:
                if cand.get("item_id") == primary_item.item_id:
                    primary_item_dict = cand
                    break

        gate_passes = (
            doc_type_value != DocType.UNRESOLVED.value
            and primary_item_dict is not None
            and primary_item_dict.get("item_type") != ItemType.DEFAULT.value
        )

        if gate_passes and primary_item is not None:
            # Step 1 slug match (Step 2/3 LLM CLASSIFY_DOC is Ph-2)
            try:
                slugs = await self._storage.find_doc_id_slugs_for_item(
                    primary_item.item_id, DocType(doc_type_value)
                )
            except Exception:
                slugs = []
            if not slugs:
                # NEW_DOCUMENT short-circuit -- derive a slug from filename + rev1
                doc_id_slug = self._slug_from_filename(attachment.filename)
                rev_number = 1

        # ---- Step D: FR-86 storage matrix dispatch ------------------------
        nsd_path_type = self._select_nsd_path_type(
            doc_type_value=doc_type_value,
            routing_resolution=routing_resolution,
            primary_item_dict=primary_item_dict,
            slug_determined=(doc_id_slug is not None and rev_number is not None),
            gate_passes=gate_passes,
        )

        # ---- Steps E + F: storage write + post-write hooks ----------------
        # The actual NSD write + index-row + associations are best-performed
        # by the caller (workflow_engine task body) since the storage Protocol
        # here is intentionally minimal. We return the RoutedAttachment with
        # all decisions; the caller does add_document_index_row +
        # add_document_item_association per match. Tests verify the routing
        # decision and the storage call counts.
        return RoutedAttachment(
            file_hash=attachment.file_hash,
            matches=tuple(matches),
            doc_type=doc_type_value,
            doc_id_slug=doc_id_slug,
            rev_number=rev_number,
            classification_resolution=cls_resolution,
            routing_resolution=routing_resolution,
            inferred_tg_name=inferred_tg,
            nsd_path_type=nsd_path_type,
            is_duplicate=False,
        )

    # ------------------------------------------------------------------
    # Internal pipeline steps
    # ------------------------------------------------------------------

    def _classify_doc_type(
        self, filename: str
    ) -> tuple[str, ClassificationResolution]:
        """Branch A: FR-85 2-step ladder. Ph-1 first cut runs Step 1 only;
        Step 2 (LLM CLASSIFY_DOC_TYPE) is Ph-1 next pass -- stub-with-skip
        to UNRESOLVED on regex miss."""
        rules = self._rules()
        matched_doc_types: list[str] = []
        for doc_type_value, patterns in rules.items():
            for pat in patterns:
                if pat.search(filename or ""):
                    matched_doc_types.append(doc_type_value)
                    break
        if len(matched_doc_types) == 1:
            return matched_doc_types[0], ClassificationResolution.FILENAME_REGEX
        # Multi-match OR no-match -> Step 2 LLM (Ph-1 next pass) -> Ph-1 first cut
        # stub: skip LLM, return UNRESOLVED.
        return DocType.UNRESOLVED.value, ClassificationResolution.UNRESOLVED_LOW_CONFIDENCE

    async def _route_to_items(
        self,
        attachment: InboundAttachment,
        candidate_items: list[dict],
    ) -> tuple[list[AttachmentItemMatch], RoutingResolution]:
        """Branch B: FR-52 5-step routing. Returns (matches, resolution)."""
        filename = (attachment.filename or "").lower()

        # ---- Step B1: strict substring match ----
        # item_description per FR-82 is nested list-of-lists; for Ph-1 first
        # cut treat as a flat comma-separated tag list OR list of strings.
        b1_matches: list[AttachmentItemMatch] = []
        for cand in candidate_items:
            tags = self._extract_tags(cand.get("item_description"))
            if not tags:
                continue
            if all(t.lower() in filename for t in tags):
                b1_matches.append(
                    AttachmentItemMatch(
                        item_id=cand["item_id"],
                        confidence=1.0,
                        source=RoutingResolution.SUBSTRING_MATCH,
                    )
                )
        if b1_matches:
            return b1_matches, RoutingResolution.SUBSTRING_MATCH

        # ---- Step B2: fuzzy match via rapidfuzz ----
        b2_matches: list[AttachmentItemMatch] = []
        for cand in candidate_items:
            item_name = (cand.get("item_name") or "").lower()
            if not item_name:
                continue
            score = fuzz.partial_ratio(filename, item_name) / 100.0
            if score >= self._fuzzy_threshold:
                b2_matches.append(
                    AttachmentItemMatch(
                        item_id=cand["item_id"],
                        confidence=score,
                        source=RoutingResolution.FUZZY_MATCH,
                    )
                )
        if b2_matches:
            # Pick the top scorer (Ph-1: single-match happy path)
            b2_matches.sort(key=lambda m: m.confidence, reverse=True)
            return [b2_matches[0]], RoutingResolution.FUZZY_MATCH

        # ---- Step B3: FR-77 Type-2 folder routing ----
        # Gated by DeliveryItemBase.folder_routing_enabled (denormalized per [D-106]).
        # Basic flow scenario: folder_routing_enabled=False -> skipped.
        any_folder_routing_enabled = any(
            cand.get("folder_routing_enabled") for cand in candidate_items
        )
        if any_folder_routing_enabled:
            # Ph-1 first cut placeholder -- folder routing implementation will
            # consume customizations/template_schemas/<customer_id>/folder_routing.yaml.
            # When folder routing matches, return RoutingResolution.FOLDER_ROUTING.
            pass

        # ---- Step B4: LLM ROUTE_ATTACHMENT ----
        if self._llm is not None:
            try:
                from core.src.llm.protocol import LLMRequest, TaskKind

                req = LLMRequest(
                    task=TaskKind.ROUTE_ATTACHMENT,
                    inputs={
                        "filename": attachment.filename,
                        "candidate_items": candidate_items,
                    },
                )
                resp = await self._llm.invoke(req)
                # Expected output: {"matches": [{"item_id": str, "confidence": float}, ...]}
                raw_matches = resp.output.get("matches", []) or []
                b4_matches: list[AttachmentItemMatch] = []
                for m in raw_matches:
                    if not isinstance(m, dict):
                        continue
                    iid = m.get("item_id")
                    conf = float(m.get("confidence", 0.0))
                    if iid and conf >= self._llm_confidence_threshold:
                        b4_matches.append(
                            AttachmentItemMatch(
                                item_id=str(iid),
                                confidence=conf,
                                source=RoutingResolution.LLM_ROUTE_ATTACHMENT,
                            )
                        )
                if b4_matches:
                    return b4_matches, RoutingResolution.LLM_ROUTE_ATTACHMENT
            except PipelineError:
                # LLM failure -- fall through to staged-default per FR-78
                logger.warning(
                    "LLM ROUTE_ATTACHMENT failed for file_hash=%s; falling to staged-default",
                    attachment.file_hash,
                )

        # ---- Step B5: staged to milestone Default work-item per FR-78 ----
        default_item = next(
            (c for c in candidate_items if c.get("item_type") == ItemType.DEFAULT.value),
            None,
        )
        if default_item is not None:
            return (
                [
                    AttachmentItemMatch(
                        item_id=default_item["item_id"],
                        confidence=0.0,
                        source=RoutingResolution.STAGED_DEFAULT,
                    )
                ],
                RoutingResolution.STAGED_DEFAULT,
            )

        # No candidate Default item -- empty matches (caller decides; storage
        # invariant requires at least one association)
        return [], RoutingResolution.STAGED_DEFAULT

    @staticmethod
    def _extract_tags(item_description: Any) -> list[str]:
        """Extract flat tag list from FR-82 nested item_description.

        Accepted shapes (Ph-1 lenient):
        - None / "" -> []
        - "tag1,tag2,tag3" (comma-separated string)
        - ["tag1", "tag2"] (flat list)
        - [["tag1", "tag2"], ["tag3"]] (nested list-of-lists per FR-82) -- flattened
        """
        if not item_description:
            return []
        if isinstance(item_description, str):
            return [t.strip() for t in item_description.split(",") if t.strip()]
        if isinstance(item_description, list):
            tags: list[str] = []
            for entry in item_description:
                if isinstance(entry, str):
                    tags.append(entry.strip())
                elif isinstance(entry, list):
                    for sub in entry:
                        if isinstance(sub, str):
                            tags.append(sub.strip())
            return [t for t in tags if t]
        return []

    @staticmethod
    def _slug_from_filename(filename: str) -> str:
        """Derive a stable doc_id_slug from a filename (Ph-1 simplification)."""
        base = filename.rsplit(".", 1)[0]
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", base).strip("_").lower() or "doc"

    def _select_nsd_path_type(
        self,
        *,
        doc_type_value: str,
        routing_resolution: RoutingResolution,
        primary_item_dict: dict | None,
        slug_determined: bool,
        gate_passes: bool,
    ) -> NSDPathType:
        """FR-86 4-path matrix dispatch per MODULE.md Step D.

        Rules:
        - routing_resolution=StagedDefault                       -> UNROUTED
        - alignment FAILS (item_type vs doc_type misaligned)     -> STAGED_NOT_CLASSIFIED
        - alignment passes BUT [D-039] Step 3 staged (no slug)   -> STAGED_NOT_REVISION
        - alignment passes AND slug determined                   -> CLASSIFIED
        """
        if routing_resolution == RoutingResolution.STAGED_DEFAULT:
            return NSDPathType.UNROUTED

        # Alignment check (FR-86 alignment invariant)
        if primary_item_dict is not None:
            item_type = primary_item_dict.get("item_type")
            aligned = self._fr86_aligned(item_type, doc_type_value)
            if not aligned:
                return NSDPathType.STAGED_NOT_CLASSIFIED

        if gate_passes and not slug_determined:
            return NSDPathType.STAGED_NOT_REVISION

        if slug_determined:
            return NSDPathType.CLASSIFIED

        # Gate didn't pass (doc_type=UNRESOLVED OR primary item is Default)
        # -> if Default already triggered StagedDefault above; otherwise staged_not_classified
        return NSDPathType.STAGED_NOT_CLASSIFIED

    @staticmethod
    def _fr86_aligned(item_type: str | None, doc_type_value: str) -> bool:
        """FR-86 alignment invariant per MODULE.md Invariants.

        Aligned pairs:
        - (test_tech_waiver_report, {test_report, tech_report, waiver})
        - (compliance_certification_release_notes, compliance_certification_release_notes)
        - (Confirmation, *) -- Confirmation items have item_type Confirmation;
          doc_type is informational (Ph-1: not strictly aligned but accepted)
        - (Default, *) -- Default items accept anything (catch-all)

        Misaligned pairs land on STAGED_NOT_CLASSIFIED per FR-86.
        """
        if not item_type:
            return False
        if item_type == ItemType.DEFAULT.value:
            return True
        if item_type == ItemType.CONFIRMATION.value:
            return True
        if item_type == ItemType.TEST_TECH_WAIVER_REPORT.value:
            return doc_type_value in {
                DocType.TEST_REPORT.value,
                DocType.TECH_REPORT.value,
                DocType.WAIVER.value,
            }
        if item_type == ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value:
            return doc_type_value == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value
        return False
