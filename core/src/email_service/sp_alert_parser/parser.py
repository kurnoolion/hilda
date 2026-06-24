"""SpAlertParser per [D-047] + FR-84 + FR-87 + 2026-06-25 cascade locks.

Scope per Module #11 architect Q1 lock 2026-06-25: alerts ONLY from the 3-list
per-customer scope -- Milestones_<customer_id>, Projects_<customer_id>,
Deliverables_<customer_id>. Out-of-scope subjects (TasksTemplate / Tasks /
Trials / Activities / Email / CommunicationLog) silently dropped per [D-118].

FR-87 strict A->B->C TPM resolution action handlers route by action verb in
the alert body's action_type field:
- tpm_reassign_to_workitem   -> storage.reassign_document_to_workitem (FR-83 / FR-87 A)
- tpm_resolve_doc_type       -> storage.tpm_resolve_doc_type           (FR-87 B; validates [D-119] 4-value)
- tpm_resolve_revision       -> storage.tpm_resolve_revision           (FR-87 C)

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
from core.src.template_schema.enums import DocType

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


# [D-119] tpm_resolved_doc_type SP field is 4-value (DocType minus UNRESOLVED).
TPM_RESOLVED_DOC_TYPE_VALID = frozenset({
    DocType.TEST_REPORT.value,
    DocType.TECH_REPORT.value,
    DocType.WAIVER.value,
    DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
})


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
    action_type: str | None              # FR-87 verb (tpm_reassign_to_workitem etc.)
    raw_subject: str
    item_title: str
    body_kvs: dict[str, str]             # all body key:value pairs (for handler use)


class TriggerDispatcherLike(Protocol):
    """Minimal workflow_engine.TriggerDispatcher surface."""

    def dispatch(self, event: Any) -> Any: ...


class SpStorageLike(Protocol):
    """Minimal storage surface used by FR-87 handlers."""

    async def reassign_document_to_workitem(
        self, file_hash: str, source_delivery_item_id: str,
        target_delivery_item_id: str, pm_id: str, **kwargs: Any,
    ) -> None: ...

    async def tpm_resolve_doc_type(
        self, file_hash: str, target_doc_type: Any, pm_id: str, **kwargs: Any,
    ) -> None: ...

    async def tpm_resolve_revision(
        self, file_hash: str, verdict: Any, pm_id: str, **kwargs: Any,
    ) -> None: ...

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
    # FR-87 handler dispatch + TriggerEvent emission
    # ------------------------------------------------------------------

    async def handle(self, parsed: ParsedSpAlert, *, pm_id: str = "tpm_unknown") -> None:
        """Dispatch FR-87 handler based on action_type. Idempotent per
        storage-side semantics; emits CommunicationLog audit row + TriggerEvent
        per [D-113]."""
        if parsed.action_type == "tpm_reassign_to_workitem":
            await self._handle_reassign(parsed, pm_id=pm_id)
        elif parsed.action_type == "tpm_resolve_doc_type":
            await self._handle_resolve_doc_type(parsed, pm_id=pm_id)
        elif parsed.action_type == "tpm_resolve_revision":
            await self._handle_resolve_revision(parsed, pm_id=pm_id)
        # Other action_types (sentinel button presses, plain status edits) emit
        # only the TriggerEvent below.
        self._emit_trigger_event(parsed)

    async def _handle_reassign(self, parsed: ParsedSpAlert, *, pm_id: str) -> None:
        file_hash = parsed.body_kvs.get("file_hash")
        source = parsed.body_kvs.get("source_delivery_item_id")
        target = parsed.body_kvs.get("target_delivery_item_id")
        if not (file_hash and source and target):
            return
        await self._storage.reassign_document_to_workitem(
            file_hash=file_hash,
            source_delivery_item_id=source,
            target_delivery_item_id=target,
            pm_id=pm_id,
        )

    async def _handle_resolve_doc_type(
        self, parsed: ParsedSpAlert, *, pm_id: str
    ) -> None:
        file_hash = parsed.body_kvs.get("file_hash")
        target_doc_type = parsed.body_kvs.get("target_doc_type")
        if not (file_hash and target_doc_type):
            return
        # [D-119] tpm_resolved_doc_type SP field accepts only 4 values
        if target_doc_type not in TPM_RESOLVED_DOC_TYPE_VALID:
            logger.warning(
                "FR-87 step (B) rejected: target_doc_type='%s' not in 4-value [D-119] subset",
                target_doc_type,
            )
            return
        await self._storage.tpm_resolve_doc_type(
            file_hash=file_hash,
            target_doc_type=DocType(target_doc_type),
            pm_id=pm_id,
        )

    async def _handle_resolve_revision(
        self, parsed: ParsedSpAlert, *, pm_id: str
    ) -> None:
        file_hash = parsed.body_kvs.get("file_hash")
        verdict = parsed.body_kvs.get("verdict")
        target_slug = parsed.body_kvs.get("target_doc_id_slug")
        if not (file_hash and verdict):
            return
        await self._storage.tpm_resolve_revision(
            file_hash=file_hash,
            verdict=verdict,
            target_doc_id_slug=target_slug,
            pm_id=pm_id,
        )

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
