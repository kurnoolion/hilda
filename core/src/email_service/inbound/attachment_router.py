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

import os
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

__all__ = [
    "Fr52AttachmentRouter",
    "StorageBackend",
    "TgResolverProtocol",
    "load_doc_type_rules",
    "_singleton_alignment_doc_type",
]


def _singleton_alignment_doc_type(item_type: str) -> "DocType | None":
    """AUTO-CLASSIFY-RELNOTES-1 (2026-08-27): when the given item_type has
    exactly ONE FR-86-aligned doc_type, return it -- else None.

    Used by both the router's Branch A auto-promotion and unrouted_ops's
    manual-route auto-promotion: an UNRESOLVED doc landing on such an item
    can be safely auto-classified without a TPM click.

    Currently ONE item_type has singleton alignment:
      compliance_certification_release_notes -> COMPLIANCE_CERTIFICATION_RELEASE_NOTES

    Excluded intentionally:
      * test_tech_waiver_report -- 3 valid doc_types (test_report, tech_report,
        waiver); ambiguous, TPM must pick.
      * default / Confirmation -- accept any doc_type per FR-86; no promotion
        signal.
    """
    if item_type == ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value:
        return DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES
    return None

logger = logging.getLogger(__name__)

# RTRC-1 (Ph-2 2026-08-02): env-gated ROUTE_TRACE. Off by default; set
# HILDA_ROUTE_TRACE=true on hilda-worker to enable per-attachment routing
# decision trace. Companion to the pre/post-router trace in
# inbound_attachment.py so the whole pipeline can be reconstructed from
# `grep ROUTE_TRACE`.
_ROUTE_TRACE = os.getenv("HILDA_ROUTE_TRACE", "").lower() in ("1", "true", "yes")


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
    async def item_has_association(self, file_hash: str, delivery_item_id: str) -> bool: ...
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
        ph1_first_pass_substring_only: bool = False,
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
        # Ph-1 first-pass scope per architect 2026-06-29:
        #   - Branch B: ONLY Step B1 (substring on item_description); skip
        #     fuzzy/folder/LLM/default-WI fallback. Returns empty matches when
        #     B1 doesn't match -> Step D routes to unrouted NSD path.
        #   - Step C (new-vs-revision): skipped (Ph-2 multi-revision per [D-066]).
        #     Slug + rev_number stay None; Step D picks staged_revision path.
        self._ph1_first_pass_substring_only = ph1_first_pass_substring_only
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
        # ---- Step 0: file_hash lookup ([D-039] Step 0) --------------------
        # Split into 0a (file-bytes-existence check for storage skip) and 0b
        # (item-association filter). Fix 2026-07-07 cross-device shared docs:
        # a single regulatory certificate legitimately re-arrives for items on
        # multiple devices within the same milestone. The prior early-return
        # short-circuited routing entirely, blocking new device associations.
        # Now: file bytes get skipped (already stored), but item routing still
        # runs and creates associations for items on this device that don't
        # yet carry the file.
        existing = await self._storage.get_document_index_row_by_hash(
            attachment.file_hash
        )
        is_duplicate_bytes = existing is not None

        # ---- Branch A: FR-85 doc_type classification ----------------------
        # When duplicate, reuse the cached doc_type from the index row to avoid
        # a redundant regex/LLM pass. Router-side classification is derived
        # from the FILE, not the delivery context, so caching is correct.
        if is_duplicate_bytes:
            doc_type_value = str(getattr(existing, "doc_type", DocType.UNRESOLVED.value))
            cls_resolution = ClassificationResolution.FILENAME_REGEX
        else:
            doc_type_value, cls_resolution = self._classify_doc_type(attachment.filename)

        # ---- Branch B: FR-52 item routing ---------------------------------
        matches, routing_resolution = await self._route_to_items(
            attachment, candidate_items
        )

        # ---- Step 0b: filter out items that already carry this file -------
        # (per cross-device fix 2026-07-07): duplicate-bytes only means "the
        # file exists in the doc index"; each item still needs its own filter
        # to prevent double-counting doc_count_received when an owner resends
        # the same email OR re-attaches the same file to items already
        # associated on this device.
        if is_duplicate_bytes and matches:
            filtered: list[AttachmentItemMatch] = []
            for m in matches:
                has_assoc = await self._storage.item_has_association(
                    attachment.file_hash, m.item_id
                )
                if not has_assoc:
                    filtered.append(m)
            matches = filtered

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

        # AUTO-CLASSIFY-RELNOTES-1 (2026-08-27): singleton-alignment auto-
        # promotion. When the filename-regex classifier came back UNRESOLVED
        # (no rule matched) AND the routed item's item_type has exactly ONE
        # FR-86-aligned doc_type (currently: compliance_certification_release_notes),
        # promote doc_type inline before Step C's gate_passes evaluates.
        # This lets Step C fill in slug + rev=1 this cycle and Step D dispatch
        # CLASSIFIED -- file lands directly at rev1/, no STAGED bounce, no
        # TPM Reclassify click. Safe because: (1) only fires when NO rule
        # matched at all -- misaligned-but-recognized doc_types (e.g. a
        # "Test Results" file resolving to test_report on a release-notes
        # slot) still stage for TPM review; (2) release_notes items accept
        # exactly this one doc_type per FR-86, so the promotion is
        # unambiguous. See also unrouted_ops.route_unrouted_to_item for the
        # symmetric manual-route auto-promotion.
        if (doc_type_value == DocType.UNRESOLVED.value
                and primary_item_dict is not None):
            _singleton = _singleton_alignment_doc_type(
                primary_item_dict.get("item_type") or ""
            )
            if _singleton is not None:
                logger.warning(
                    "AUTO_CLASSIFY_RELNOTES: promoting UNRESOLVED -> %s "
                    "for item=%s (item_type=%s) filename=%r file_hash=%s",
                    _singleton.value, primary_item.item_id,
                    primary_item_dict.get("item_type"),
                    attachment.filename, attachment.file_hash[:12],
                )
                doc_type_value = _singleton.value
                cls_resolution = ClassificationResolution.FILENAME_REGEX

        gate_passes = (
            doc_type_value != DocType.UNRESOLVED.value
            and primary_item_dict is not None
            and primary_item_dict.get("item_type") != ItemType.DEFAULT.value
        )

        # Ph-1 first pass per architect 2026-06-29 -- corrected 2026-06-29
        # (post live-test bug discovery):
        # Step C MULTI-REVISION lookup (existing-slug resolution + LLM
        # CLASSIFY_DOC) is Ph-2. Ph-1 always treats files as NEW_DOCUMENT,
        # rev=1, slug derived from filename. The earlier impl forced
        # gate_passes=False which suppressed the NEW_DOCUMENT slug assignment
        # too, sending classified files into _staged_classification path.
        # Bug was: 'no multi-revision handling in Ph-1' got misread as
        # 'no slug + rev assignment in Ph-1'; the actual requirement is
        # 'no slug LOOKUP across prior revisions in Ph-1'.
        if gate_passes and primary_item is not None:
            slugs: list = []
            if not self._ph1_first_pass_substring_only:
                # Ph-2: look up existing slugs for this (item, doc_type)
                # to determine if this is a new revision of an existing doc.
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
        # When the file bytes are already on disk (is_duplicate_bytes=True),
        # reuse the existing index row's slug/rev so downstream persist skips
        # the redundant DocumentIndexRow insert (which is idempotent but the
        # explicit skip keeps telemetry clean and avoids a wasted round-trip).
        if is_duplicate_bytes and existing is not None:
            doc_id_slug = getattr(existing, "doc_id_slug", doc_id_slug)
            rev_number = getattr(existing, "rev_number", rev_number)
            inferred_tg = getattr(existing, "inferred_tg_name", inferred_tg)

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
            is_duplicate=is_duplicate_bytes,
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
        # NSDMATCH-2 (2026-08-24): tag-match input = attachment.match_hint
        # when set (NSD ingest supplies the immediate parent folder name),
        # else falls back to filename. `filename` var stays for doc-type
        # regex classification which is orthogonal to tag routing.
        filename = (attachment.filename or "").lower()
        match_input = (attachment.match_hint or attachment.filename or "").lower()

        # ---- Step B1: strict substring match per FR-82 + architect 2026-06-29 ----
        # item_description is list-of-lists with AND-of-OR semantics:
        #   outer list = OR (any group matching is enough)
        #   inner list = AND (every tag in the group must appear in match_input)
        # Examples:
        #   [["Sustainability"]]                       -> match if match_input contains "Sustainability"
        #   [["SDoc"], ["Qualification", "Product"]]   -> match if match_input contains "SDoc"
        #                                                  OR (contains both "Qualification" AND "Product")
        #   [["5G", "LC"]]                             -> match if match_input contains both "5G" AND "LC"
        # Earlier flat-AND impl was incorrect; broke architect live test 2026-06-29
        # ("Sustainability" file didn't match item with [["Sustainability"]] tag).
        #
        # Per architect 2026-07-22 refinement (D-151): TG-scoped shortcuts +
        # `["default"]` tag semantics. See _tg_scoped_route for details.
        b1_matches, b1_resolution = self._tg_scoped_route(match_input, candidate_items)
        if b1_matches:
            return b1_matches, b1_resolution

        # Ph-1 first pass per architect 2026-06-29 + D-151 2026-07-22:
        # substring-only mode. Skip B2/B3/B4 entirely; jump straight to
        # milestone Default WI (B5) — TG-scoped routing already ran above.
        if self._ph1_first_pass_substring_only:
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
            return [], RoutingResolution.STAGED_DEFAULT

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

    def _tg_scoped_route(
        self, filename: str, candidate_items: list[dict],
    ) -> tuple[list[AttachmentItemMatch], RoutingResolution]:
        """D-151 per architect 2026-07-22 — 4-stage TG-scoped substring routing.

        Groups candidates by tg_name and applies, per TG independently:
          Stage 0: TG has exactly 1 work item (excluding Default WIs; Default
                   WI is milestone-level) → route to it. Resolution:
                   TG_SINGLE_ITEM.
          Stage 1: Step B1 substring match on item_description tag-sets in the TG.
                     * Exactly 1 match → route. Resolution: SUBSTRING_MATCH.
                     * N>1 matches AND one has ["default"] tag-set →
                       route to it. Resolution: TG_DEFAULT_MULTIMATCH.
                     * N>1 matches AND none has ["default"] → fall through
                       (no match returned for this TG).
                     * 0 matches → Stage 2.
          Stage 2: TG-default fallback: if any item in the TG has ["default"]
                   tag-set → route to it. Resolution: TG_DEFAULT_NOMATCH.

        Returns the FIRST TG that produced a match, ranked by:
          TG_DEFAULT_MULTIMATCH > SUBSTRING_MATCH > TG_SINGLE_ITEM > TG_DEFAULT_NOMATCH.
        If no TG produced a match, returns ([], SUBSTRING_MATCH) — caller
        falls through to milestone Default WI (Step B5).

        Multiple `["default"]` items in the same TG: template validator
        rejects at load (per architect Q5). Runtime defensively takes the
        first-in-iteration-order winner + logs a warning.
        """
        # Group by tg_name; Default WIs (item_type='default') are milestone-
        # level and excluded from the TG-scoped pass.
        by_tg: dict[str, list[dict]] = {}
        for c in candidate_items:
            if (c.get("item_type") or "").lower() == ItemType.DEFAULT.value.lower():
                continue
            tg = c.get("tg_name") or ""
            if not tg:
                continue
            by_tg.setdefault(tg, []).append(c)

        if _ROUTE_TRACE:
            logger.warning(
                "ROUTE_TRACE stage=by_tg filename=%r buckets=%s",
                filename, {tg: len(items) for tg, items in by_tg.items()},
            )

        # D-153 architect 2026-07-25: a doc lives under exactly ONE TG folder
        # in the view tree (view/<cust>/<dev>/<mile>/<tg>/<...>) — the router
        # therefore MUST NEVER route a single doc to items in multiple TGs.
        # Any cross-TG evidence collapses to the milestone Default WI so the
        # TPM can triage. Inside a single TG, the existing D-151 4-stage
        # pipeline resolves (single-match, ["default"] tiebreaker, or None
        # for ambiguous multi-match → also falls to Default WI per rule 1).
        #
        # "Evidence" per TG = at least one item's item_description tag-set
        # substring-matches the filename. TG_SINGLE_ITEM (Ph-2 shortcut) is
        # a SEPARATE signal handled after the evidence pass — it fires only
        # when NO TG has substring evidence and exactly ONE TG qualifies.

        per_tg: dict[str, tuple[AttachmentItemMatch | None, RoutingResolution, bool]] = {}
        for tg_name, items in by_tg.items():
            match, resolution = self._route_within_tg(filename, tg_name, items)
            has_evidence = self._any_substring_hit(filename, items)
            per_tg[tg_name] = (match, resolution, has_evidence)

        tgs_with_evidence = [tg for tg, (_, _, ev) in per_tg.items() if ev]

        if _ROUTE_TRACE:
            summary = {
                tg: {
                    "match": m.item_id if m else None,
                    "resolution": str(r),
                    "evidence": ev,
                } for tg, (m, r, ev) in per_tg.items()
            }
            logger.warning(
                "ROUTE_TRACE stage=per_tg filename=%r tgs_with_evidence=%s per_tg=%s",
                filename, tgs_with_evidence, summary,
            )

        # Case A: >1 TG has substring evidence → cross-TG ambiguity → Default WI.
        # Case B: exactly 1 TG has evidence → use that TG's resolution (may
        #         itself be None for intra-TG multi-match no ["default"] →
        #         still falls to Default WI per rule 1).
        # Case C: no TG has evidence → try TG_SINGLE_ITEM shortcut, but only
        #         if EXACTLY ONE TG has a valid single-item result. Multiple
        #         solo-item TGs → cross-TG → Default WI.
        if len(tgs_with_evidence) > 1:
            return [], RoutingResolution.SUBSTRING_MATCH  # Default WI

        if len(tgs_with_evidence) == 1:
            tg = tgs_with_evidence[0]
            match, resolution, _ = per_tg[tg]
            if match is None:
                # intra-TG multi-match no ["default"] → Default WI (rule 1)
                return [], RoutingResolution.SUBSTRING_MATCH
            return [match], resolution

        # Case C — no substring evidence anywhere. Ph-2 TG_SINGLE_ITEM
        # shortcut may still fire; Ph-1 gate disables it entirely (see
        # _route_within_tg Stage 0). Enforce cross-TG constraint here too:
        # multiple 1-item TGs → Default WI, not fan-out.
        tg_single_hits = [
            (tg, m, r) for tg, (m, r, _) in per_tg.items()
            if m is not None and r == RoutingResolution.TG_SINGLE_ITEM
        ]
        if len(tg_single_hits) == 1:
            _, m, r = tg_single_hits[0]
            return [m], r

        # TDN-1 (2026-08-02): TG_DEFAULT_NOMATCH hits — a TG has a
        # ["default"]-tagged item and 0 matches happened for the filename in
        # that TG. Route to it ONLY when exactly one TG qualifies:
        #   * Ph-2 (owner-per-TG → 1 TG in scope): unambiguous → route
        #   * Ph-1 (universal TPM → many TGs in scope): if every TG has its
        #     own default, we'd have N candidates and no principled tiebreak
        #     → fall to milestone Default WI (TPM triages via _unknownTG)
        # Zero TG-default hits → also fall to milestone Default WI.
        tg_default_nomatch_hits = [
            (tg, m, r) for tg, (m, r, _) in per_tg.items()
            if m is not None and r == RoutingResolution.TG_DEFAULT_NOMATCH
        ]
        if len(tg_default_nomatch_hits) == 1:
            _, m, r = tg_default_nomatch_hits[0]
            return [m], r

        # 0 or >1 TG_SINGLE_ITEM/TG_DEFAULT_NOMATCH hits → fall to milestone
        # Default WI.
        return [], RoutingResolution.SUBSTRING_MATCH

    def _route_within_tg(
        self,
        filename: str,
        tg_name: str,
        items: list[dict],
    ) -> tuple[AttachmentItemMatch | None, RoutingResolution]:
        """Apply the TG-scoped routing WITHIN a single TG's items per D-151
        Ph-1 refinement 2026-07-22 architect Q1/Q2 answers.

        Ph-1 ordering (substring first, TG=1 shortcut second):
          Stage 1: substring match on item_description tag-sets.
                     * Exactly 1 match → SUBSTRING_MATCH.
                     * N>1 matches + one has ["default"] → TG_DEFAULT_MULTIMATCH.
                     * N>1 matches, none has ["default"] → ambiguous, return None
                       (caller falls to milestone Default WI).
                     * 0 matches → try Stage 0 fallback below.
          Stage 0 (fallback): TG=1 implicit routing.
                     * TG has exactly 1 non-Default item → route to it,
                       resolution=TG_SINGLE_ITEM.
                     * Otherwise → return None.

        Substring-first ordering per architect Q1 2026-07-22: when the same
        owner spans multiple TGs (early-access Ph-1 test scenario), TG=1
        short-circuits without checking tags would over-fire. Running substring
        first lets a doc with distinctive tags win in the correct TG via
        SUBSTRING_MATCH (which beats TG_SINGLE_ITEM per precedence in
        _tg_scoped_route); a TG with 1 item + no tag hit still catches via
        the TG_SINGLE_ITEM fallback. Under production shape (1 owner = 1 TG),
        the reorder is idempotent-in-decision.

        Ph-2 deferred: TG_DEFAULT_NOMATCH (a TG's ["default"]-tagged item
        catching Stage-0-missed docs) is out of scope in Ph-1 per architect
        2026-07-22 to keep the fan-out surface small during early-access
        testing. STATUS.md Flag tracks the Ph-2 restore. Enum value
        RoutingResolution.TG_DEFAULT_NOMATCH remains defined but unused
        at runtime; test coverage is skipped with pytest.skip pending Ph-2.
        """
        # Stage 1: substring match on item_description tag-sets.
        matches: list[dict] = []
        for cand in items:
            groups = self._extract_tag_groups(cand.get("item_description"))
            if not groups:
                continue
            if self._any_group_matches(filename, groups):
                matches.append(cand)

        if _ROUTE_TRACE:
            logger.warning(
                "ROUTE_TRACE stage=within_tg filename=%r tg=%s items=%d matches=%s",
                filename, tg_name, len(items),
                [m["item_id"] for m in matches],
            )

        if len(matches) == 1:
            if _ROUTE_TRACE:
                logger.warning(
                    "ROUTE_TRACE stage=within_tg_decision filename=%r tg=%s "
                    "branch=single_match winner=%s",
                    filename, tg_name, matches[0]["item_id"],
                )
            return (
                AttachmentItemMatch(
                    item_id=matches[0]["item_id"],
                    confidence=1.0,
                    source=RoutingResolution.SUBSTRING_MATCH,
                ),
                RoutingResolution.SUBSTRING_MATCH,
            )

        if len(matches) > 1:
            # Multi-match: TG-default tiebreaker per D-151.
            default_items = [c for c in matches if self._has_default_tag_set(c)]
            if _ROUTE_TRACE:
                logger.warning(
                    "ROUTE_TRACE stage=within_tg_decision filename=%r tg=%s "
                    "branch=multi_match matches=%s default_items=%s",
                    filename, tg_name,
                    [m["item_id"] for m in matches],
                    [d["item_id"] for d in default_items],
                )
            if len(default_items) >= 1:
                if len(default_items) > 1:
                    logger.warning(
                        "attachment_router: TG %r has %d items marked with ['default'] "
                        "tag-set — template config error; taking first (item_id=%s)",
                        tg_name, len(default_items), default_items[0]["item_id"],
                    )
                return (
                    AttachmentItemMatch(
                        item_id=default_items[0]["item_id"],
                        confidence=1.0,
                        source=RoutingResolution.TG_DEFAULT_MULTIMATCH,
                    ),
                    RoutingResolution.TG_DEFAULT_MULTIMATCH,
                )
            # Multi-match with no ["default"] item AMONG THE MATCHES.
            # TDN-1 (2026-08-02): if the TG has a separate ["default"]-tagged
            # item that didn't itself substring-match, route to it. Semantic:
            # "TG has a designated default → all ambiguity inside that TG
            # collapses to its default." Extends D-151's TG-default tiebreaker
            # to cover the "default sits in a different item than any of the
            # matched ones" case.
            tg_defaults_all = [c for c in items if self._has_default_tag_set(c)]
            if len(tg_defaults_all) >= 1:
                if _ROUTE_TRACE:
                    logger.warning(
                        "ROUTE_TRACE stage=within_tg_decision filename=%r tg=%s "
                        "branch=multi_match_tg_default winner=%s",
                        filename, tg_name, tg_defaults_all[0]["item_id"],
                    )
                return (
                    AttachmentItemMatch(
                        item_id=tg_defaults_all[0]["item_id"],
                        confidence=1.0,
                        source=RoutingResolution.TG_DEFAULT_NOMATCH,
                    ),
                    RoutingResolution.TG_DEFAULT_NOMATCH,
                )
            # Fall through to caller so B5 (milestone Default WI) can catch.
            return (None, RoutingResolution.SUBSTRING_MATCH)

        # Stage 0 fallback (was Stage 0 shortcut in initial D-151):
        # TG=1 implicit routing per architect Q2 2026-07-22 refinement.
        # Only fires when Stage 1 substring produced 0 matches AND the TG has
        # exactly 1 non-Default work item.
        #
        # Ph-1 gate 2026-07-25: skipped entirely when ph1_first_pass_substring_only
        # is True (per architect early-access review of Doc 3 failure). In Ph-1
        # early-access shape (1 TPM = many TGs), TG_SINGLE_ITEM would fire on
        # ANY 1-item TG whenever the filename had no substring evidence
        # anywhere, sending files intended for a different TG into a solo-item
        # TG. In Ph-2 production shape (1 owner = 1 TG), this fallback becomes
        # trivially correct — but Ph-2 depends on owner-scoped candidate
        # filtering (not yet implemented). Restored via the same flag flip
        # that turns on B2/B3/B4. Tracked in STATUS.md Flag.
        if len(items) == 1 and not self._ph1_first_pass_substring_only:
            return (
                AttachmentItemMatch(
                    item_id=items[0]["item_id"],
                    confidence=1.0,
                    source=RoutingResolution.TG_SINGLE_ITEM,
                ),
                RoutingResolution.TG_SINGLE_ITEM,
            )

        # TDN-1 (2026-08-02): TG_DEFAULT_NOMATCH re-enabled after the Ph-2
        # deferral (originally 2026-07-22). Semantic: 0 matches inside this
        # TG, but the TG has a designated ["default"]-tagged item → route to
        # it as a per-TG catchall.
        #
        # The Ph-1-vs-Ph-2 concern that drove the original deferral (universal
        # owner + all-TGs candidates → many TGs each with a default → arbitrary
        # tiebreak needed) is now handled ONE LEVEL UP in _tg_scoped_route Case
        # C: it counts how many TGs returned TG_DEFAULT_NOMATCH and routes only
        # when exactly one qualifies, else falls to milestone Default WI. So we
        # can safely return the per-TG default here.
        default_items_all = [c for c in items if self._has_default_tag_set(c)]
        if len(default_items_all) >= 1:
            if len(default_items_all) > 1:
                logger.warning(
                    "attachment_router: TG %r has %d items marked with "
                    "['default'] tag-set — template config error; taking first "
                    "(item_id=%s)",
                    tg_name, len(default_items_all), default_items_all[0]["item_id"],
                )
            if _ROUTE_TRACE:
                logger.warning(
                    "ROUTE_TRACE stage=within_tg_decision filename=%r tg=%s "
                    "branch=nomatch_tg_default winner=%s",
                    filename, tg_name, default_items_all[0]["item_id"],
                )
            return (
                AttachmentItemMatch(
                    item_id=default_items_all[0]["item_id"],
                    confidence=1.0,
                    source=RoutingResolution.TG_DEFAULT_NOMATCH,
                ),
                RoutingResolution.TG_DEFAULT_NOMATCH,
            )

        # No match, TG=1 shortcut didn't apply, no ["default"] item → reject.
        return (None, RoutingResolution.SUBSTRING_MATCH)

    def _any_substring_hit(self, filename: str, items: list[dict]) -> bool:
        """D-153 helper: True if any item in `items` has an item_description
        tag-set that substring-matches `filename` (per FR-82 AND-of-OR shape).

        Used by _tg_scoped_route to detect per-TG evidence — the cross-TG
        constraint (a doc can never route to items in multiple TGs) requires
        us to know which TGs the doc had ANY substring evidence in, not just
        the TGs that produced a confident single-item resolution.
        """
        for cand in items:
            groups = self._extract_tag_groups(cand.get("item_description"))
            if not groups:
                continue
            if self._any_group_matches(filename, groups):
                return True
        return False

    # D-154 architect 2026-07-26 — reserved literal `all-15-digits-imei` for
    # IMEI-shaped Excel filenames. The IMEI is a 15-digit unique identifier
    # per handset; every doc has a different IMEI so substring tags cannot
    # cover the case. When the reserved literal appears as a standalone
    # tag-group entry (like `["default"]` per D-151), the router matches the
    # item iff the filename basename contains a word-bounded 15-digit IMEI
    # token AND ends in an Excel extension.
    #
    # D-154 addendum (same-day widening 2026-07-26): initially the literal
    # required the basename to be EXACTLY 15 digits + ext. Observed real
    # Ph-1 traffic includes IMEIs embedded as substrings like
    # `Report_357123456789012_Samsung.xlsx`. Widened to match "contains a
    # 15-digit IMEI token" — case 1 (exact) is a subset of case 2 (contains)
    # since the exact form has start-of-string and `.` as its delimiters.
    #
    # Word-boundary guard prevents a 15-digit run INSIDE a longer number
    # from matching: `1234567890123456789.xlsx` (19 digits) has 5 different
    # 15-digit substrings but NONE are word-bounded (all surrounded by
    # digits). The IMEI token must be delimited by non-digit or edges.
    #
    # Reserved literal isolation is enforced by DeliveryItemBase validator
    # (mixed groups like ["imei", "all-15-digits-imei"] are rejected at
    # template load), so runtime can trust the shape.
    _IMEI_XLS_TAG = "all-15-digits-imei"
    # 15-digit IMEI as a word-bounded token — delimited by non-digit or edges.
    # (?:^|\D) = start-of-string OR non-digit before. Consumes 1 char except
    # at start; that's fine — re.search anywhere in basename catches the token.
    _IMEI_TOKEN_REGEX = re.compile(r"(?:^|\D)\d{15}(?:\D|$)")
    # Tabular extension anchored at end of basename. Added `csv` 2026-07-27
    # per architect observation: real Ph-1 IMEI-shaped filenames arrive as
    # .csv exports, not just Excel binary formats. Same reserved-literal
    # semantics apply — the IMEI file is IMEI-shaped tabular data regardless
    # of container format. Tag name `all-15-digits-imei` kept as-is per
    # D-154 addendum renaming-rejected rationale.
    _IMEI_EXT_REGEX = re.compile(r"\.(xls|xlsx|xlsm|xlsb|csv)$", re.IGNORECASE)

    @classmethod
    def _filename_matches_imei_excel(cls, filename: str) -> bool:
        """True if filename basename CONTAINS a word-bounded 15-digit IMEI
        token AND ends in a tabular extension (.xls/.xlsx/.xlsm/.xlsb/.csv).

        Function name kept as `_imei_excel` for callsite stability; the
        extension list is the source of truth for what counts as "tabular"
        here (see _IMEI_EXT_REGEX).

        Covers all observed shapes:
          - Exact: `357123456789012.xlsx` / `357123456789012.csv`
          - Embedded: `Report_357123456789012_Samsung.xlsx`
          - CSV export: `imei_357123456789012_log.csv`

        Rejects false positives:
          - `1234567890123456789.xlsx` (19-digit run — no 15-digit word-bounded token)
          - `imei_357123456789012.pdf` (non-tabular extension)
          - `14-digit-only.xlsx` (only 14 digits somewhere)

        `filename` is already lowercased by caller; regexes are
        case-insensitive on ext for defense against a contract change.
        """
        # PurePosixPath.name — filename may arrive with a path prefix in some
        # paths; be defensive.
        base = filename.rsplit("/", 1)[-1]
        if not cls._IMEI_EXT_REGEX.search(base):
            return False
        return cls._IMEI_TOKEN_REGEX.search(base) is not None

    def _any_group_matches(self, filename: str, groups: list[list[str]]) -> bool:
        """Return True if ANY tag-group in `groups` matches `filename`.

        A group matches when either:
          (a) every inner tag is a substring of `filename` (normal FR-82
              AND-of-OR substring semantics), OR
          (b) D-154 reserved literal: the group is exactly
              `[cls._IMEI_XLS_TAG]` AND filename passes _filename_is_imei_excel.

        Case-insensitive on both sides (filename is lowercased upstream).
        """
        for group in groups:
            # D-154 reserved literal — matches iff filename contains a
            # word-bounded 15-digit IMEI token AND is an Excel file.
            if (
                len(group) == 1
                and isinstance(group[0], str)
                and group[0].strip().lower() == self._IMEI_XLS_TAG
            ):
                if self._filename_matches_imei_excel(filename):
                    if _ROUTE_TRACE:
                        logger.warning(
                            "ROUTE_TRACE stage=group_match filename=%r "
                            "hit_group=%s kind=imei_excel",
                            filename, group,
                        )
                    return True
                continue  # explicit skip — don't fall into substring path
            # Normal FR-82 substring AND-of-OR.
            if all(tag.lower() in filename for tag in group):
                if _ROUTE_TRACE:
                    logger.warning(
                        "ROUTE_TRACE stage=group_match filename=%r "
                        "hit_group=%s kind=substring",
                        filename, group,
                    )
                return True
        return False

    @staticmethod
    def _has_default_tag_set(cand: dict) -> bool:
        """True if candidate item's item_description contains ["default"] as
        a standalone tag-set entry (per D-151). The literal "default" must
        appear alone; the DeliveryItemBase model validator rejects mixed
        tag-sets like ["waiver", "default"] at template load, so runtime
        can trust the shape."""
        desc = cand.get("item_description")
        if not isinstance(desc, list):
            return False
        for tag_set in desc:
            if not isinstance(tag_set, list):
                continue
            if len(tag_set) == 1 and isinstance(tag_set[0], str) \
                    and tag_set[0].strip().lower() == "default":
                return True
        return False

    @staticmethod
    def _extract_tag_groups(item_description: Any) -> list[list[str]]:
        """Extract AND-of-OR tag groups from FR-82 nested item_description.

        Architect semantics 2026-06-29:
          outer list = OR  (any group matching is enough to route)
          inner list = AND (every tag in the group must appear in filename)

        Canonical shape: list[list[str]] (e.g. [["Sustainability"]] is one
        group with one tag; [["SDoc"], ["Qualification", "Product"]] is two
        groups: OR(AND("SDoc"), AND("Qualification","Product"))).

        Lenient input shapes (Ph-1; SP serializers may emit any of these):
        - None / "" / "null"                       -> []
        - "tag1,tag2,tag3"  (legacy CSV)           -> [["tag1"], ["tag2"], ["tag3"]]
                                                       (each tag becomes its own OR group;
                                                        back-compat with flat string fields)
        - ["tag1", "tag2"]  (flat list)            -> [["tag1"], ["tag2"]]
                                                       (each tag becomes its own OR group)
        - [["A", "B"], ["C"]]  (nested, canonical) -> [["A", "B"], ["C"]]
        """
        if not item_description:
            return []
        if isinstance(item_description, str):
            # Legacy CSV: each tag becomes its own one-element AND group
            # (so substring match is true if filename contains ANY of the tags)
            return [[t.strip()] for t in item_description.split(",") if t.strip()]
        if isinstance(item_description, list):
            groups: list[list[str]] = []
            for entry in item_description:
                if isinstance(entry, str):
                    if entry.strip():
                        groups.append([entry.strip()])
                elif isinstance(entry, list):
                    inner = [
                        s.strip() for s in entry
                        if isinstance(s, str) and s.strip()
                    ]
                    if inner:
                        groups.append(inner)
            return groups
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
