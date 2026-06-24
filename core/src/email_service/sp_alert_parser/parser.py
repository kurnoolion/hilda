"""SpAlertParser per [D-047] + 2026-06-26 cascade locks.

Scope per Module #11 architect Q1 lock 2026-06-25: alerts ONLY from the 3-list
per-customer scope -- Milestones_<customer_id>, Projects_<customer_id>,
Deliverables_<customer_id>. Out-of-scope subjects (TasksTemplate / Tasks /
Trials / Activities / Email / CommunicationLog) silently dropped per [D-118].

FR-87 step A/B/C TPM resolution handlers were REMOVED 2026-06-26 per [D-122]
direct-POST architecture cascade. FR-87 resolution flows now go directly from
TPM browser to HILDA's dashboard module via POST /docs/<customer_id>/<sp_id>/
resolve_reassign + resolve_doc_type endpoints (step C still Ph-2 deferred).
This module retains ONLY [D-047] entity-change SP-alert routing -- parses
the alert, emits TriggerEvent for rule_engine matching.

email_service is a TRIGGER SOURCE per [D-113] -- it emits TriggerEvent
(with item_snapshot when applicable) via workflow_engine.TriggerDispatcher.dispatch(...).
HILDA does NOT call SpClient directly; SP writes flow through tracker /
workflow_engine -> sharepoint_integration per [D-117] NTLM digest-dance.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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


# Module #11 Q1 architect lock 2026-06-25: 3-list scope; per-customer suffixes
# (e.g., Milestones_acme). The parser strips the customer suffix when matching.
ALERT_SCOPE_LISTS = frozenset({"Milestones", "Projects", "Deliverables"})


@dataclass(frozen=True)
class AlertRoutingKey:
    list_name: str             # base list (Milestones / Projects / Deliverables)
    list_suffix: str           # customer_id suffix (e.g., "acme")
    project_id: str | None
    milestone_name: str | None
    item_number: int | None


@dataclass(frozen=True)
class ParsedSpAlert:
    routing_key: AlertRoutingKey
    action_type: str | None              # entity-change action verb (post-2026-06-26: no FR-87 verbs)
    raw_subject: str
    item_title: str
    body_kvs: dict[str, str]             # all body key:value pairs


class TriggerDispatcherLike(Protocol):
    """Minimal workflow_engine.TriggerDispatcher surface."""

    def dispatch(self, event: Any) -> Any: ...


class SpStorageLike(Protocol):
    """Minimal storage surface for SP-alert audit logging.

    Per [D-122] cascade 2026-06-26: FR-87 action handlers (reassign / resolve_doc_type
    / resolve_revision) REMOVED -- FR-87 now flows TPM browser -> dashboard direct
    POST. This protocol retains only log_communication for [D-047] entity-change
    audit trail.
    """

    async def log_communication(self, row: Any) -> None: ...


# Subject regex shared with classifier (re-compiled here so this module
# remains importable independently).
_ALERT_SUBJECT_RE = re.compile(
    r"^Alert_(?P<list>[A-Za-z]+)_(?P<suffix>[A-Za-z0-9]+)\s*-\s*(?P<title>.+)$"
)


class SpAlertParser:
    """Parses SP alert emails + dispatches FR-87 handlers + emits TriggerEvent.

    Per [D-118] SP UI engineer provisioning boundary: HILDA CONSUMES alerts
    from pre-provisioned SP lists; HILDA does NOT create lists, columns, or
    "Anything changes" alert subscriptions.
    """

    def __init__(
        self,
        storage: SpStorageLike,
        trigger_dispatcher: TriggerDispatcherLike | None = None,
    ) -> None:
        self._storage = storage
        self._dispatcher = trigger_dispatcher

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self, msg: InboundMessage) -> ParsedSpAlert | None:
        """Return a ParsedSpAlert when subject matches a 3-list scope alert;
        return None when subject is out-of-scope (logged + dropped per [D-118]).
        Raises EML-E007 when the subject matched but routing key is missing.
        """
        subject_match = _ALERT_SUBJECT_RE.match(msg.subject or "")
        if not subject_match:
            return None

        list_name = subject_match.group("list")
        list_suffix = subject_match.group("suffix")
        title = subject_match.group("title")

        # 3-list scope check per Module #11 architect Q1 lock 2026-06-25
        if list_name not in ALERT_SCOPE_LISTS:
            logger.info(
                "sp_alert_out_of_scope: subject_list=%s; dropped per [D-118]",
                list_name,
            )
            return None

        project_id, milestone_name, item_number = extract_routing_key(msg.body_text)

        # Subject matched but no routing key -> EML-E007
        if project_id is None and milestone_name is None and item_number is None:
            raise PipelineError(
                "EML-E007",
                context={},
            )

        body_kvs = self._parse_body_kvs(msg.body_text or "")
        action_type = body_kvs.get("action_type")

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
        )

    @staticmethod
    def _parse_body_kvs(body: str) -> dict[str, str]:
        out: dict[str, str] = {}
        kv_re = re.compile(
            r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$", re.MULTILINE
        )
        for m in kv_re.finditer(body):
            key = m.group(1).strip()
            val = m.group(2).strip()
            out.setdefault(key, val)
        return out

    # ------------------------------------------------------------------
    # TriggerEvent emission per [D-047]
    # ------------------------------------------------------------------

    async def handle(self, parsed: ParsedSpAlert, *, pm_id: str = "tpm_unknown") -> None:
        """Emit TriggerEvent for the SP-alert per [D-113].

        Per [D-122] cascade 2026-06-26: FR-87 action handlers (tpm_reassign_to_workitem
        / tpm_resolve_doc_type / tpm_resolve_revision) REMOVED -- those flows now go
        directly via dashboard POST endpoints. This method now only emits the
        TriggerEvent so rule_engine matches on entity-change SP alerts.
        """
        self._emit_trigger_event(parsed)

    def _emit_trigger_event(self, parsed: ParsedSpAlert) -> None:
        """Emit TriggerEvent via workflow_engine.TriggerDispatcher per [D-113].

        Ph-1 first cut: best-effort emission -- when no dispatcher is wired
        (test scenarios, --once mode without rule_engine), we silently skip.
        """
        if self._dispatcher is None:
            return
        try:
            # Lazy import to avoid hard dependency on rule_engine at module load
            from core.src.rule_engine.models import (
                EntityRef,
                TriggerEvent,
                TriggerKind,
            )

            event = TriggerEvent(
                trigger=TriggerKind.ITEM_MODIFIED,
                sub_trigger=None,
                entity_ref=EntityRef(
                    customer_id=parsed.routing_key.list_suffix,
                    milestone_id=parsed.routing_key.milestone_name,
                ),
                field_deltas=None,
                timestamp=datetime.now(timezone.utc),
                correlation_id=str(uuid.uuid4()),
                derived_fields={"action_type": parsed.action_type},
            )
            self._dispatcher.dispatch(event)
        except Exception as exc:
            logger.warning("TriggerDispatcher emit skipped: %s", str(exc)[:100])
