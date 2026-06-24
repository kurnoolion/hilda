"""SpAlertParser per [D-047] + 2026-06-26 cascade locks + 2026-06-27 SP-format cascade.

Scope per Module #11 architect Q1 lock 2026-06-25: alerts ONLY from the 3-list
per-customer scope -- Milestones, Projects, Deliverables. Out-of-scope subjects
(TasksTemplate / Tasks / Trials / Activities / Email / CommunicationLog) silently
dropped per [D-118].

**2026-06-27 cascade per architect screenshots + Q1-Q5 locks** (real SP alert
format observed; previous "Alert_<List>_<suffix>" assumption was wrong):

- Subject format actually emitted by SP:
    * Milestones (GLOBAL list per architect 2026-06-21):    "Milestones - <Title>"
    * Deliverables (per-customer list):     "Deliverables_<customer_id> - <Title>"
    * Projects (per-customer list):         "Projects_<customer_id> - <Title>"
      (Projects alerts NOT configured Ph-1 per architect Q1 2026-06-27; Ph-2)
- Customer suffix in subject for Deliverables/Projects; for Milestones the
  customer_id comes from body `carrier:` field.
- Action verb extracted from header line "<Title> has been (added|changed|deleted)".
- Body field-deltas: SP marks changed fields with "Edited" suffix; values often
  carry a leading "- " separator. extract_field_deltas() pulls these into a
  {field: new_value} dict carried in TriggerEvent.field_deltas per [D-047].
- Duplicate alerts: SP can resend; we dedupe via Message-ID LRU cache (size 1024,
  TTL 10 min) per architect 2026-06-27.
- "Changed" alert with empty field_deltas: silently dropped (no signal worth
  emitting) per architect 2026-06-27.
- Empty body fields ("key: " with no value) captured as "" not skipped per
  architect Q3 2026-06-27.

FR-87 step A/B/C TPM resolution handlers were REMOVED 2026-06-26 per [D-122]
direct-POST architecture cascade.

email_service is a TRIGGER SOURCE per [D-113] -- it emits TriggerEvent (with
item_snapshot when applicable) via workflow_engine.TriggerDispatcher.dispatch(...).
"""
from __future__ import annotations

import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.protocol import InboundMessage
from core.src.email_service.sp_alert_parser.routing_key import extract_routing_key

__all__ = [
    "ALERT_SCOPE_LISTS",
    "AlertRoutingKey",
    "ParsedSpAlert",
    "SpAlertParser",
    "TriggerDispatcherLike",
    "SpStorageLike",
]

logger = logging.getLogger(__name__)


# Module #11 Q1 architect lock 2026-06-25: 3-list scope. Projects alerts are
# NOT configured Ph-1 (Ph-2 per architect Q1 2026-06-27) but we keep the list
# in scope so if SP sends one we recognize + drop cleanly.
ALERT_SCOPE_LISTS = frozenset({"Milestones", "Projects", "Deliverables"})


@dataclass(frozen=True)
class AlertRoutingKey:
    list_name: str             # Milestones / Projects / Deliverables
    list_suffix: str           # customer_id (from subject suffix OR body `carrier:`)
    project_id: str | None
    milestone_name: str | None
    item_number: int | None


@dataclass(frozen=True)
class ParsedSpAlert:
    routing_key: AlertRoutingKey
    action_type: str | None              # "added" / "changed" / "deleted" (extracted from header)
    raw_subject: str
    item_title: str
    body_kvs: dict[str, str]             # all body key:value pairs (incl. empty values per Q3)
    field_deltas: dict[str, str]         # subset of body_kvs marked "Edited" per [D-047] + Q4


class TriggerDispatcherLike(Protocol):
    """Minimal workflow_engine.TriggerDispatcher surface."""

    def dispatch(self, event: Any) -> Any: ...


class SpStorageLike(Protocol):
    """Minimal storage surface for SP-alert audit logging."""

    async def log_communication(self, row: Any) -> None: ...


# Subject regex per 2026-06-27 cascade.
# Examples:
#   "Milestones - P1"                            -> list=Milestones, suffix=None, title=P1
#   "Deliverables_MMK - Device Readiness Review" -> list=Deliverables, suffix=MMK, title=...
#   "Projects_MMK - <project_title>"             -> list=Projects, suffix=MMK, title=...
_ALERT_SUBJECT_RE = re.compile(
    r"^(?P<list>Milestones|Projects|Deliverables)"
    r"(?:_(?P<suffix>[A-Za-z0-9]+))?"
    r"\s*-\s*"
    r"(?P<title>.+)$"
)

# Action-verb header line: "<Title> has been (added|changed|deleted)".
# Title may contain arbitrary chars (incl. spaces). The header is anchored at
# start-of-line because it sits at the top of the body before kv pairs.
_ACTION_VERB_RE = re.compile(
    r"^\s*.+?\s+has\s+been\s+(?P<verb>added|changed|deleted)\b",
    re.MULTILINE | re.IGNORECASE,
)

# Per-LINE body kv extractor (applied via splitlines() to avoid multi-line bleed).
# Matches three flavors on a single line:
#   "key: value"               (no Edited marker, normal field)
#   "key: - value Edited"      (Edited marker -- changed field)
#   "key:"  or  "key: "        (empty value -- captured as "" per Q3)
# Edited marker is captured as a separate group; leading "- " stripped.
_BODY_KV_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>.*?)\s*$"
)

# After a value is captured, optional leading "- " prefix + optional trailing
# "Edited" marker are post-processed.
_EDITED_SUFFIX_RE = re.compile(r"\s+Edited\s*$", re.IGNORECASE)
_LEADING_DASH_RE = re.compile(r"^-\s+")


class _MessageIdDedup:
    """LRU-with-TTL Message-ID dedup per architect 2026-06-27.

    Bounded size: 1024. TTL: 10 minutes from first-seen. Thread-unsafe
    (callers are async + single-threaded per Celery worker).
    """

    def __init__(self, max_size: int = 1024, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._max = max_size
        self._ttl = ttl
        self._seen: OrderedDict[str, datetime] = OrderedDict()

    def is_duplicate(self, message_id: str, now: datetime) -> bool:
        # Evict TTL-expired entries lazily.
        cutoff = now - self._ttl
        while self._seen:
            oldest_id, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts < cutoff:
                self._seen.popitem(last=False)
            else:
                break
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        # Enforce size cap.
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return False


class SpAlertParser:
    """Parses SP alert emails + extracts field-deltas + emits TriggerEvent.

    Per [D-118] SP UI engineer provisioning boundary: HILDA CONSUMES alerts
    from pre-provisioned SP lists; HILDA does NOT create lists or columns.
    """

    def __init__(
        self,
        storage: SpStorageLike,
        trigger_dispatcher: TriggerDispatcherLike | None = None,
        *,
        dedup_max_size: int = 1024,
        dedup_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._storage = storage
        self._dispatcher = trigger_dispatcher
        self._dedup = _MessageIdDedup(max_size=dedup_max_size, ttl=dedup_ttl)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self, msg: InboundMessage) -> ParsedSpAlert | None:
        """Return ParsedSpAlert on success; None when alert is dropped per
        one of:
          - subject is out of 3-list scope (logged + dropped per [D-118])
          - Message-ID seen recently (duplicate SP resend per architect 2026-06-27)
          - action="changed" but field_deltas empty (no-op change per architect 2026-06-27)
          - Projects alerts (Ph-2 per architect Q1 2026-06-27)

        Raises EML-E007 when subject matched in-scope list but no routing key
        could be extracted from body (data quality issue worth surfacing).
        """
        # -- Dedup (per architect 2026-06-27) --
        now = datetime.now(timezone.utc)
        if msg.message_id and self._dedup.is_duplicate(msg.message_id, now):
            logger.info(
                "sp_alert_duplicate: message_id=%s; dropped per architect 2026-06-27",
                msg.message_id,
            )
            return None

        # -- Subject parse --
        subject_match = _ALERT_SUBJECT_RE.match(msg.subject or "")
        if not subject_match:
            return None

        list_name = subject_match.group("list")
        list_suffix = subject_match.group("suffix")  # may be None for Milestones
        title = subject_match.group("title").strip()

        if list_name not in ALERT_SCOPE_LISTS:
            logger.info(
                "sp_alert_out_of_scope: subject_list=%s; dropped per [D-118]",
                list_name,
            )
            return None

        # -- Projects: drop Ph-1 per architect Q1 2026-06-27 --
        if list_name == "Projects":
            logger.info(
                "sp_alert_projects_dropped_ph1: title=%s; configured Ph-2 per architect Q1 2026-06-27",
                title,
            )
            return None

        # -- Body parse: kv pairs + field-deltas --
        body = msg.body_text or ""
        body_kvs, field_deltas = self._parse_body(body)

        # -- Action verb from header line --
        action_type = self._extract_action_verb(body)

        # -- Customer suffix derivation --
        #   Deliverables: suffix from subject
        #   Milestones (global): suffix from body 'carrier' field
        if list_suffix is None:
            list_suffix = body_kvs.get("carrier", "")
            if not list_suffix:
                logger.warning(
                    "sp_alert_missing_customer_id: list=%s title=%s message_id=%s",
                    list_name, title, msg.message_id,
                )

        # -- Routing key extraction from body --
        project_id, milestone_name, item_number = extract_routing_key(body)

        # -- For Milestones: Title IS the milestone_name --
        if list_name == "Milestones" and milestone_name is None:
            milestone_name = title

        # -- All-None routing key check -> EML-E007 --
        if (
            project_id is None
            and milestone_name is None
            and item_number is None
        ):
            raise PipelineError("EML-E007", context={})

        # -- "Changed" with empty field_deltas: drop per architect 2026-06-27 --
        if action_type == "changed" and not field_deltas:
            logger.info(
                "sp_alert_no_change: list=%s title=%s; dropped per architect 2026-06-27",
                list_name, title,
            )
            return None

        return ParsedSpAlert(
            routing_key=AlertRoutingKey(
                list_name=list_name,
                list_suffix=list_suffix,
                project_id=project_id,
                milestone_name=milestone_name,
                item_number=item_number,
            ),
            action_type=action_type,
            raw_subject=msg.subject or "",
            item_title=title,
            body_kvs=body_kvs,
            field_deltas=field_deltas,
        )

    @staticmethod
    def _parse_body(body: str) -> tuple[dict[str, str], dict[str, str]]:
        """Single-pass body parser. Returns (all_kvs, edited_kvs).

        all_kvs: every body key:value pair (empty values captured as "" per Q3).
        edited_kvs: subset where the value carried an "Edited" suffix marker.
                    Leading "- " separator stripped from value.
        """
        all_kvs: dict[str, str] = {}
        edited_kvs: dict[str, str] = {}
        for line in body.splitlines():
            m = _BODY_KV_LINE_RE.match(line)
            if not m:
                continue
            key = m.group("key").strip()
            raw_value = m.group("value")
            if key in all_kvs:
                continue
            # Detect + strip "Edited" suffix (case-insensitive).
            edited = bool(_EDITED_SUFFIX_RE.search(raw_value))
            value = _EDITED_SUFFIX_RE.sub("", raw_value).strip()
            # Strip leading "- " separator (Edited fields render as "- value Edited").
            value = _LEADING_DASH_RE.sub("", value).strip()
            all_kvs[key] = value
            if edited:
                edited_kvs[key] = value
        return all_kvs, edited_kvs

    @staticmethod
    def _extract_action_verb(body: str) -> str | None:
        """Extract action verb from header line '<Title> has been (verb)'."""
        m = _ACTION_VERB_RE.search(body)
        if m:
            return m.group("verb").lower()
        return None

    # ------------------------------------------------------------------
    # TriggerEvent emission per [D-047]
    # ------------------------------------------------------------------

    async def handle(self, parsed: ParsedSpAlert, *, pm_id: str = "tpm_unknown") -> None:
        """Emit TriggerEvent for the SP-alert per [D-113]."""
        self._emit_trigger_event(parsed)

    def _emit_trigger_event(self, parsed: ParsedSpAlert) -> None:
        """Emit TriggerEvent via workflow_engine.TriggerDispatcher per [D-113].

        field_deltas carries the "Edited"-marked field new-values per architect
        Q4+Q5 lock 2026-06-27 (NEW values only; no extra SP roundtrip for old).
        Empty field_deltas dict on Added/Deleted is expected; on Changed we'd
        have already returned None from parse() (no-op guard).
        """
        if self._dispatcher is None:
            return
        try:
            # Lazy import to avoid hard dep on rule_engine at module load.
            from core.src.rule_engine.models import (
                EntityRef,
                TriggerEvent,
                TriggerKind,
            )

            # Map action verb to TriggerKind.
            verb_to_trigger = {
                "added":   TriggerKind.ITEM_MODIFIED,    # ItemAdded falls under ItemModified Ph-1
                "changed": TriggerKind.ITEM_MODIFIED,
                "deleted": TriggerKind.ITEM_MODIFIED,    # deletion handled as modification
            }
            trigger = verb_to_trigger.get(parsed.action_type or "", TriggerKind.ITEM_MODIFIED)

            event = TriggerEvent(
                trigger=trigger,
                sub_trigger=parsed.action_type,
                entity_ref=EntityRef(
                    customer_id=parsed.routing_key.list_suffix,
                    milestone_id=parsed.routing_key.milestone_name,
                ),
                field_deltas=dict(parsed.field_deltas) if parsed.field_deltas else None,
                timestamp=datetime.now(timezone.utc),
                correlation_id=str(uuid.uuid4()),
                derived_fields={
                    "action_type": parsed.action_type,
                    "list_name":   parsed.routing_key.list_name,
                    "item_title":  parsed.item_title,
                },
            )
            self._dispatcher.dispatch(event)
        except Exception as exc:
            logger.warning("TriggerDispatcher emit skipped: %s", str(exc)[:100])
