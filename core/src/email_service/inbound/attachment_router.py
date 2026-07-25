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
        filename = (attachment.filename or "").lower()

        # ---- Step B1: strict substring match per FR-82 + architect 2026-06-29 ----
        # item_description is list-of-lists with AND-of-OR semantics:
        #   outer list = OR (any group matching is enough)
        #   inner list = AND (every tag in the group must appear in filename)
        # Examples:
        #   [["Sustainability"]]                       -> match if filename contains "Sustainability"
        #   [["SDoc"], ["Qualification", "Product"]]   -> match if filename contains "SDoc"
        #                                                  OR (contains both "Qualification" AND "Product")
        #   [["5G", "LC"]]                             -> match if filename contains both "5G" AND "LC"
        # Earlier flat-AND impl was incorrect; broke architect live test 2026-06-29
        # ("Sustainability" file didn't match item with [["Sustainability"]] tag).
        #
        # Per architect 2026-07-22 refinement (D-151): TG-scoped shortcuts +
        # `["default"]` tag semantics. See _tg_scoped_route for details.
        b1_matches, b1_resolution = self._tg_scoped_route(filename, candidate_items)
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

        results_by_precedence: dict[RoutingResolution, list[AttachmentItemMatch]] = {}

        for tg_name, items in by_tg.items():
            match, resolution = self._route_within_tg(filename, tg_name, items)
            if match is None:
                continue
            # Accumulate per resolution so we can pick the strongest tie-break.
            results_by_precedence.setdefault(resolution, []).append(match)

        # Precedence per D-151 + architect Q3 2026-07-22 refinement:
        # strong evidence beats fallback signals; TG_DEFAULT_NOMATCH excluded
        # (Ph-2 deferred — see _route_within_tg docstring). Falls through to
        # milestone Default WI (STAGED_DEFAULT) in the caller when this
        # method returns empty.
        for res in (
            RoutingResolution.TG_DEFAULT_MULTIMATCH,
            RoutingResolution.SUBSTRING_MATCH,
            RoutingResolution.TG_SINGLE_ITEM,
            # RoutingResolution.TG_DEFAULT_NOMATCH,   # Ph-2 deferred
        ):
            if res in results_by_precedence and results_by_precedence[res]:
                return list(results_by_precedence[res]), res

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
            if any(
                all(tag.lower() in filename for tag in group)
                for group in groups
            ):
                matches.append(cand)

        if len(matches) == 1:
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
            # Multi-match with no ["default"] item → ambiguous; let caller
            # fall through so B5 (milestone Default WI) can catch.
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

        # Stage 2 TG_DEFAULT_NOMATCH deferred to Ph-2 per architect 2026-07-22.
        # Restore path when re-enabling:
        # default_items = [c for c in items if self._has_default_tag_set(c)]
        # if len(default_items) >= 1:
        #     if len(default_items) > 1:
        #         logger.warning(
        #             "attachment_router: TG %r has %d items marked with "
        #             "['default'] tag-set — template config error; taking first "
        #             "(item_id=%s)",
        #             tg_name, len(default_items), default_items[0]["item_id"],
        #         )
        #     return (
        #         AttachmentItemMatch(
        #             item_id=default_items[0]["item_id"],
        #             confidence=1.0,
        #             source=RoutingResolution.TG_DEFAULT_NOMATCH,
        #         ),
        #         RoutingResolution.TG_DEFAULT_NOMATCH,
        #     )

        # No match, TG=1 shortcut didn't apply → this TG rejects.
        return (None, RoutingResolution.SUBSTRING_MATCH)

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
