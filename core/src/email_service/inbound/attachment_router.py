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
    "keyword_fallback_doc_type",
    "sole_tg_resolver",
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


# DOCTYPE-FALLBACK-1 (2026-09-02): keyword tie-breaker for the one item_type
# that `_singleton_alignment_doc_type` cannot resolve.
#
# Maintaining doc_type_filename_rules.yaml by hand does not scale -- MMK
# already carries 77 test_report patterns and 45 release-notes patterns, each
# added after a document arrived UNRESOLVED and a TPM reclassified it. For
# item_type=test_tech_waiver_report the three valid doc_types are separable by
# a couple of filename keywords, so the ladder gets a third rung instead of
# another 77 regexes.
#
# 'waiver' is checked FIRST and deliberately as a plain substring: it is the
# one doc_type with an outward-facing consequence (waivers are never uploaded
# on the P1 submit path), and per architect 2026-09-02 every waiver filename
# carries the word from the template -- so this rung is both high-precision
# and high-recall, which is what makes the test_report default below safe.
_WAIVER_SUBSTRING = "waiver"


def filename_says_waiver(filename: str) -> bool:
    """True when the filename carries the template's 'waiver' marker.

    Deliberately item_type-independent and used as a VETO, not just a rung:
    per architect 2026-09-02 every waiver filename carries this word, so a
    name containing it must never be auto-classified as something else. See
    DOCTYPE-WAIVER-VETO-1 at the promotion site for why that matters.

    CLASSIFY-BASENAME-1 (2026-09-09): the substring check runs against the
    BASENAME only, same rule as _classify_doc_type. NSD ingest passes the
    share-relative path here (e.g.
    `VZW/7. FCC (Waiver)/Test reports/A3LSMS948U WPT RF Exposure Test Report revD.pdf`);
    without the strip the folder `(Waiver)` triggered a VETO that flipped
    the router's own TG_DEFAULT_NOMATCH-routed doc into a WAIVER
    classification -- exact reverse of the intended semantics (folder =
    routing, filename = classification).
    """
    from pathlib import PurePosixPath
    basename = PurePosixPath(filename or "").name
    return _WAIVER_SUBSTRING in basename.lower()

# 'technical report' / 'tech report' as a PHRASE, plus 'TR' as a whole token.
# Bare 'report' is intentionally NOT a signal: nearly every *test* report
# filename contains it (see the .*testreport.* and .*test_result.* rules
# below), so keying on it would invert the two majority buckets.
#
# 'TR' must be delimited -- as a bare substring it matches Control, Central,
# Extract, Transmit, Strategic, Country, Spectrum. Same failure mode as the
# NSD2 denylist's 'CHA' matching 'Charging' (fixed 2026-09-01).
_TECH_REPORT_RE = re.compile(
    r"(?:tech(?:nical)?[_\-\s]*report)"      # technical report / tech_report
    r"|(?:(?:^|[_\-\s(])TR(?:[_\-\s).]|$))",  # TR as its own segment
    re.IGNORECASE,
)


def keyword_fallback_doc_type(
    filename: str, item_type: str
) -> "DocType | None":
    """Last-rung doc_type guess from `filename`, scoped by `item_type`.

    Returns None -- meaning "leave UNRESOLVED, let it stage for TPM" -- unless
    `item_type` is the one whose FR-86-aligned set is
    {test_report, tech_report, waiver}. Scoping matters: without it a
    release-notes slot could be handed `test_report` and land misaligned.

    Ladder, in order:
      1. 'waiver' anywhere in the name -> WAIVER
      2. 'technical report' / 'tech report' / 'TR' token -> TECH_REPORT
      3. anything else -> TEST_REPORT

    Rung 3 is a genuine default rather than a match, which is only sound
    because rung 1 catches every waiver (see _WAIVER_SUBSTRING). If that
    template convention ever changes, rung 3 must become opt-in per TG.
    """
    if item_type != ItemType.TEST_TECH_WAIVER_REPORT.value:
        return None
    # CLASSIFY-BASENAME-1 (2026-09-09): fold every keyword check onto the
    # basename, same rule as _classify_doc_type + filename_says_waiver.
    # NSD ingest passes the share-relative path; without the strip a folder
    # named 'Technical Reports/foo.pdf' would promote foo.pdf to TECH_REPORT
    # from the folder, not the filename. Rung 3's TEST_REPORT default is
    # unaffected (any non-empty basename lands there), but rungs 1 and 2 must
    # be filename-only.
    from pathlib import PurePosixPath
    name = PurePosixPath((filename or "").strip()).name
    if not name:
        return None
    if filename_says_waiver(name):
        return DocType.WAIVER
    if _TECH_REPORT_RE.search(name):
        return DocType.TECH_REPORT
    return DocType.TEST_REPORT

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
    async def get_max_rev_for_slug(self, milestone_id: str, doc_id_slug: str) -> int: ...
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


def sole_tg_resolver(
    candidate_items: list[dict],
    sender: str = "",
    to_addrs: tuple[str, ...] = (),
    cc_addrs: tuple[str, ...] = (),
) -> str | None:
    """UNROUTED-TG-SCOPE-1 (2026-09-03): channel-agnostic TG resolver.

    Returns the single distinct `tg_name` across `candidate_items`, or None
    when the candidates span more than one TG (or carry none).

    Conforms to TgResolverProtocol, so it drops into the existing
    `tg_resolver` slot -- which was wired to None for EVERY channel, meaning
    `document_index.inferred_tg_name` was never populated at ingest despite
    the column, FR-78 and [D-060] all existing for it.

    Why this shape rather than per-channel plumbing: the candidate set is
    already TG-scoped upstream on exactly the channels that need it.
    _filter_hw_pl_nsd2_items gates NSD ingest to tg_name=='HW PL', and PLM
    ingest runs inside _process_tg_group, one TG per ticket per [D-035]. So
    "all candidates share one TG" is true by construction there, and the
    single TG falls out without either path having to pass it down.

    Email is the case that legitimately returns None: while one TPM owns
    items across many TGs, an outreach batch spans TGs and there is no single
    answer. That is the correct outcome -- the manual-route dropdown then
    offers every item in the milestone rather than guessing a scope.

    `sender` / `to_addrs` / `cc_addrs` are accepted for protocol conformance
    and unused; email identity-based resolution lives in
    tg_resolver.resolve_tg_from_email and is a separate lookup.
    """
    _ = (sender, to_addrs, cc_addrs)
    tgs = {
        (c.get("tg_name") or "").strip()
        for c in (candidate_items or [])
    }
    tgs.discard("")
    if len(tgs) == 1:
        return next(iter(tgs))
    return None


# DOCTYPE-EXT-1 (2026-09-02): one canonical document-extension set, applied
# to every rule at load time.
#
# The MMK rules file had THREE different trailing extension groups across its
# 123 patterns, so a document's doc_type depended on which rule happened to
# list its extension:
#     90x  pdf|doc|docx|xlsx|pptx
#     32x  pdf|doc|docx|xlsx|pptxi|html|htm     <- 'pptxi' is a typo for pptx,
#                                                 so these 32 compliance rules
#                                                 accepted a non-existent
#                                                 extension and REJECTED real
#                                                 .pptx files
#      1x  pdf|doc|docx|xlsx|xlsm|pptx
#
# and `ppt` was absent from all three. Live consequence: three
# '<device> Waiver Request_*.ppt' files matched NO rule, went UNRESOLVED,
# and were then auto-promoted to compliance_certification_release_notes by
# singleton alignment -- filed as delivered release notes at rev1, aligned,
# with no TPM signal. Rewriting at load time rather than editing 123 YAML
# lines keeps the config readable and makes the set impossible to skew again.
_CANONICAL_DOC_EXTENSIONS: tuple[str, ...] = (
    "pdf", "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx", "html", "htm",
)
_CANONICAL_EXT_GROUP = r"\.(" + "|".join(_CANONICAL_DOC_EXTENSIONS) + r")$"

# Matches a trailing `\.(a|b|c)$` extension group. All 123 MMK patterns and
# all default patterns end in exactly this shape; anything that doesn't is
# left untouched and logged.
_TRAILING_EXT_GROUP_RE = re.compile(r"\\\.\([^)]*\)\$$")


# DOCTYPE-PRECEDENCE-1 (2026-09-02): explicit doc_type precedence, replacing
# reliance on YAML key order.
#
# `_classify_doc_type` previously returned UNRESOLVED whenever more than one
# doc_type matched, which sent the document to singleton auto-promotion and
# could file a waiver as a compliance release note. Multi-match now resolves
# by this order instead. Waiver is first because it is the only doc_type with
# an outward-facing consequence: waivers are never uploaded on the P1 submit
# path, so a waiver classified as anything else is SENT to the carrier.
# Ordering lives in code, not in the YAML, so re-sorting the config file
# cannot silently change classification behaviour.
_DOC_TYPE_PRECEDENCE: tuple[str, ...] = (
    DocType.WAIVER.value,
    DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
    DocType.TECH_REPORT.value,
    DocType.TEST_REPORT.value,
)


def _canonicalize_extension_group(regex: str) -> tuple[str, bool]:
    """Replace a trailing extension group with the canonical set.

    Returns `(regex, rewritten)`. Patterns without the expected trailing
    shape are returned unchanged with rewritten=False.
    """
    if _TRAILING_EXT_GROUP_RE.search(regex):
        return _TRAILING_EXT_GROUP_RE.sub(
            _CANONICAL_EXT_GROUP.replace("\\", "\\\\"), regex
        ), True
    return regex, False


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
    rewritten = 0
    left_alone: list[str] = []
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
            # DOCTYPE-EXT-1: normalise the extension group so classification
            # never depends on which rule happened to list an extension.
            regex, did = _canonicalize_extension_group(regex)
            if did:
                rewritten += 1
            else:
                left_alone.append(f"{doc_type}:{regex[:60]}")
            compiled_list.append(re.compile(regex, flags))
        if compiled_list:
            compiled[doc_type] = compiled_list
    logger.warning(
        "DOCTYPE_RULES: loaded %s doc_types from %s -- %d pattern(s) "
        "extension-normalised to %s, %d left as-is%s",
        sorted(compiled), rules_path, rewritten,
        list(_CANONICAL_DOC_EXTENSIONS), len(left_alone),
        f" ({left_alone[:3]})" if left_alone else "",
    )
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
            # DOCTYPE-WAIVER-VETO-1 (2026-09-02): a filename carrying the
            # template's 'waiver' marker must never be auto-promoted to
            # another doc_type. Live case: three '..._Waiver Request_*.ppt'
            # files missed Step 1 (extension gap, since fixed by
            # DOCTYPE-EXT-1), routed onto a compliance item, and singleton
            # alignment filed them as compliance_certification_release_notes
            # -- aligned and CLASSIFIED at rev1. Classifying them WAIVER
            # instead makes the pair misaligned, so they stage for TPM.
            # Staging a waiver is recoverable; shipping one to the carrier
            # because it was mislabelled is not.
            if filename_says_waiver(attachment.filename):
                logger.warning(
                    "DOCTYPE_WAIVER_VETO: filename=%r says waiver -- "
                    "declining auto-promotion to %s on item=%s "
                    "(item_type=%s); classifying WAIVER file_hash=%s",
                    attachment.filename,
                    _singleton.value if _singleton else "keyword-fallback",
                    primary_item.item_id,
                    primary_item_dict.get("item_type"),
                    attachment.file_hash[:12],
                )
                doc_type_value = DocType.WAIVER.value
                cls_resolution = (
                    ClassificationResolution.FILENAME_FALLBACK_KEYWORD
                )
            elif _singleton is not None:
                logger.warning(
                    "AUTO_CLASSIFY_RELNOTES: promoting UNRESOLVED -> %s "
                    "for item=%s (item_type=%s) filename=%r file_hash=%s",
                    _singleton.value, primary_item.item_id,
                    primary_item_dict.get("item_type"),
                    attachment.filename, attachment.file_hash[:12],
                )
                doc_type_value = _singleton.value
                cls_resolution = ClassificationResolution.FILENAME_REGEX
            else:
                # DOCTYPE-FALLBACK-1: no singleton alignment, so try the
                # item_type-scoped keyword ladder. Returns None for every
                # item_type except test_tech_waiver_report, leaving the doc
                # UNRESOLVED to stage for TPM exactly as before.
                _fallback = keyword_fallback_doc_type(
                    attachment.filename,
                    primary_item_dict.get("item_type") or "",
                )
                if _fallback is not None:
                    logger.warning(
                        "DOCTYPE_FALLBACK: promoting UNRESOLVED -> %s for "
                        "item=%s (item_type=%s) filename=%r file_hash=%s "
                        "-- keyword fallback, no YAML rule matched",
                        _fallback.value, primary_item.item_id,
                        primary_item_dict.get("item_type"),
                        attachment.filename, attachment.file_hash[:12],
                    )
                    doc_type_value = _fallback.value
                    cls_resolution = (
                        ClassificationResolution.FILENAME_FALLBACK_KEYWORD
                    )

        gate_passes = (
            doc_type_value != DocType.UNRESOLVED.value
            and primary_item_dict is not None
            and primary_item_dict.get("item_type") != ItemType.DEFAULT.value
        )

        # Step C -- new-vs-revision determination per [D-039].
        #
        # REV-1 (2026-08-30, user design lock): the multi-revision lookup is no
        # longer gated on ph1_first_pass_substring_only. That flag stays True
        # in production and continues to gate Steps B2/B3/B4 (fuzzy / folder /
        # LLM routing) and the TG_SINGLE_ITEM Stage 0 shortcut -- only THIS
        # block was ungated, so enabling revision families does not re-open the
        # 2026-07-25 TG_SINGLE_ITEM mis-routing regression.
        #
        # Prior behaviour: rev_number was ALWAYS 1 and the slug was the raw
        # filename stem. A same-filename resend therefore produced a second
        # index row colliding on uq_doc_slug_rev (milestone, slug, rev) and
        # re-derived the SAME internal NSD path, overwriting revision 1's bytes
        # on disk. A different-filename resend forked into an unrelated family.
        #
        # New behaviour: the version-stripped stem is matched against the
        # item's existing slugs for this doc_type. A hit continues that family
        # at max(rev)+1; a miss opens a new family at rev 1. Either way slug +
        # rev are populated, so Step D dispatches CLASSIFIED rather than
        # bouncing the file to _staged_revision.
        if gate_passes and primary_item is not None:
            candidate_slug = self._slug_from_filename(attachment.filename)
            slugs: list = []
            try:
                slugs = await self._storage.find_doc_id_slugs_for_item(
                    primary_item.item_id, DocType(doc_type_value)
                )
            except Exception:
                # Storage hiccup -- degrade to NEW_DOCUMENT rather than
                # staging the file. Worst case is a fork into a fresh family,
                # which the TPM can re-merge; staging would strand the file.
                slugs = []

            # Legacy rows carry un-stripped slugs (`report_v2`), so normalize
            # BOTH sides before comparing and keep the STORED spelling as the
            # family key -- rewriting it would orphan existing rows and the
            # uq_doc_slug_rev index entries that reference them.
            family_slug: str | None = None
            for existing_slug in slugs:
                if self._slug_from_filename(existing_slug) == candidate_slug:
                    family_slug = existing_slug
                    break

            if family_slug is not None:
                milestone_id = (primary_item_dict or {}).get("milestone_id") or ""
                next_rev = 1
                try:
                    next_rev = await self._storage.get_max_rev_for_slug(
                        milestone_id, family_slug
                    ) + 1
                except Exception:
                    # Same rationale as above -- never strand the file. rev 1
                    # may collide on the unique index, which surfaces loudly
                    # rather than silently overwriting bytes.
                    logger.warning(
                        "REV-1: max-rev lookup failed for milestone=%s slug=%r "
                        "-- falling back to rev 1",
                        milestone_id, family_slug,
                    )
                doc_id_slug = family_slug
                rev_number = next_rev
                logger.info(
                    "REV-1: revision %d of family %r (item=%s filename=%r)",
                    rev_number, family_slug, primary_item.item_id,
                    attachment.filename,
                )
            else:
                # NEW_DOCUMENT -- fresh family at rev 1.
                doc_id_slug = candidate_slug
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
        to UNRESOLVED on regex miss.

        CLASSIFY-BASENAME-1 (2026-09-09): classification is filename-only.
        NSD ingest passes the share-relative path here (e.g.
        `VZW/14. PTCRB (Waiver)/Certi/SM-<...>.pdf`); PLM does something
        similar; email hands a bare basename. Running the doc-type regexes
        against the whole string let folder segments leak into
        classification -- a document sitting under a folder named
        `PTCRB (Waiver)` matched the waiver pattern from the FOLDER, not
        the file. Per user 2026-09-09: folder names drive ROUTING (via
        match_hint / item_description tags), filename drives
        CLASSIFICATION; keep them separate. Doing the strip inside the
        classifier means every current and future caller benefits without
        having to remember. When the resulting basename matches no
        pattern the classifier stays UNRESOLVED -- the doc STAGES and a
        TPM reclassifies, which under WAIVER-UNIVERSAL-1 is a one-click
        operation regardless of the routed item's item_type.
        """
        from pathlib import PurePosixPath
        basename = PurePosixPath(filename or "").name
        rules = self._rules()
        matched_doc_types: list[str] = []
        for doc_type_value, patterns in rules.items():
            for pat in patterns:
                if pat.search(basename):
                    matched_doc_types.append(doc_type_value)
                    break
        if len(matched_doc_types) == 1:
            return matched_doc_types[0], ClassificationResolution.FILENAME_REGEX
        if len(matched_doc_types) > 1:
            # DOCTYPE-PRECEDENCE-1 (2026-09-02): resolve by explicit
            # precedence instead of returning UNRESOLVED.
            #
            # Returning UNRESOLVED here was actively harmful, not merely
            # unhelpful: it handed the document to the singleton-alignment
            # auto-promotion downstream, which would classify it from the
            # ROUTED ITEM's item_type. A file named '..._Waiver Request_WPC
            # Certi....' matching both the waiver rule and a compliance rule
            # therefore got filed as a compliance release note -- aligned and
            # CLASSIFIED at rev1, with no staging and no TPM signal.
            for candidate in _DOC_TYPE_PRECEDENCE:
                if candidate in matched_doc_types:
                    logger.warning(
                        "DOCTYPE_PRECEDENCE: filename=%r matched %s -- "
                        "resolving to %s by precedence",
                        basename, sorted(matched_doc_types), candidate,
                    )
                    return candidate, ClassificationResolution.FILENAME_REGEX
            # Every match is outside the precedence list (a doc_type added to
            # the YAML but not to _DOC_TYPE_PRECEDENCE). Stage rather than
            # guess, and say so loudly.
            logger.warning(
                "DOCTYPE_PRECEDENCE: filename=%r matched %s, none of which "
                "are in the precedence list %s -- leaving UNRESOLVED",
                basename, sorted(matched_doc_types),
                list(_DOC_TYPE_PRECEDENCE),
            )
        # No match -> Step 2 LLM (Ph-1 next pass) -> Ph-1 first cut
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
        """Derive a stable doc_id_slug from a filename.

        REV-1 (2026-08-30): trailing version tokens are stripped so that an
        owner resending the same document under a decorated name lands in the
        SAME revision family. `report.xlsx`, `report_v2.xlsx`, `report v3.xls`,
        `report_rev2.docx` and `report (1).xlsx` all slug to `report`.

        Deliberately conservative -- only the token shapes the user locked
        2026-08-30 are stripped:
          * a trailing `(N)` counter, removed from the RAW base name before
            normalization (so it never collapses into the generic `_N` form)
          * a trailing `_v<N>` or `_rev<N>` on the normalized slug, applied
            repeatedly for stacked suffixes (`report_v2_rev3` -> `report`)

        A bare trailing `_<digits>` is NOT stripped: real filenames carry
        date-ish tails (`..._SWMK_V4_1104.docx`) where the digits are part of
        the document's identity, and stripping them would merge unrelated
        documents into one family. Those stay distinct families -- the safe
        failure direction (a missed merge is visible; a wrong merge is not).
        """
        base = filename.rsplit(".", 1)[0]
        # `(N)` counter on the raw base, e.g. "report (1)" / "report(2)".
        base = re.sub(r"\s*\(\d+\)\s*$", "", base)
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", base).strip("_").lower()
        # Stacked `_v2` / `_rev3` tails; loop so `report_v2_rev3` collapses.
        while True:
            stripped = re.sub(r"[_-]?(?:rev|v)\d+$", "", slug)
            if stripped == slug:
                break
            slug = stripped
        return slug.strip("_-") or "doc"

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
        - (*, waiver) -- WAIVER-UNIVERSAL-1 (2026-09-09): a waiver is legitimate
          evidence on ANY item_type. Waivers arrive over email during DRR (and
          occasionally other milestones); they never upload to the carrier (the
          waiver early-return in resolve_carrier_destination enforces that), so
          the only effect of "misaligned" here was to STAGE waivers on
          compliance_certification_release_notes items and demand a bogus
          reclassify. Per user 2026-09-09: waivers are always legit; skip the
          item-type gate. Handled BEFORE the item_type table so it applies even
          when item_type is None / unknown.
        - (test_tech_waiver_report, {test_report, tech_report, waiver})
        - (compliance_certification_release_notes, compliance_certification_release_notes)
        - (Confirmation, *) -- Confirmation items have item_type Confirmation;
          doc_type is informational (Ph-1: not strictly aligned but accepted)
        - (Default, *) -- Default items accept anything (catch-all)

        Misaligned pairs land on STAGED_NOT_CLASSIFIED per FR-86.
        """
        if doc_type_value == DocType.WAIVER.value:
            return True
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
