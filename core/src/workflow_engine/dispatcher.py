"""TriggerDispatcher -- the central rule_engine -> Celery dispatch surface.

Receives `TriggerEvent`s from the 6 trigger-source sites, calls `rule_engine.evaluate`
with an `item_snapshot` (per D2 + D12 cascade 2026-06-23), and schedules each returned
`RuleMatch`'s action chain as an independent Celery chain per [D-066].

Pause check (FR-31 sub-1) reads `item.rules_paused: bool` from the snapshot per D5
cascade 2026-06-23 -- no PauseStateLookup Protocol (dropped per [D-112]; SP column
is the canonical home per [D-108]).

Pure orchestration -- workflow_engine does not execute action logic; downstream
modules own the actual work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from celery import Celery

from core.src.diagnostics import format_code
from core.src.rule_engine import RuleEngine, RuleMatch, TriggerEvent

from .celery_app import hilda_celery_app
from .registry import build_chain_from_rule_match

__all__ = ["TriggerDispatcher", "DispatchResult", "StorageLike"]


logger = logging.getLogger(__name__)


class StorageLike(Protocol):
    """Minimal storage Protocol for TriggerDispatcher -- fetches item snapshot for
    pause check. Concrete `storage` module implements this via structural typing.

    Per D12 cascade 2026-06-23: dispatcher reads item BEFORE calling
    rule_engine.evaluate so pause check happens at evaluation time.
    """

    def get_delivery_item(self, delivery_item_id: str) -> Any: ...


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one TriggerDispatcher.dispatch() call."""

    correlation_id:  str
    matched_count:   int                          # rule_engine matches before pause filter
    scheduled_tasks: list[str] = field(default_factory=list)        # Celery task IDs
    skipped_matches: list[tuple[str, Literal["paused"]]] = field(default_factory=list)


class TriggerDispatcher:
    """Receives TriggerEvent from any of the 6 trigger sources; resolves matches
    via rule_engine; schedules independent Celery chains per [D-066]."""

    def __init__(
        self,
        rule_engine: RuleEngine,
        storage: StorageLike | None = None,    # None ok for item-less event tests
        celery_app: Celery = hilda_celery_app,
    ) -> None:
        self._rule_engine = rule_engine
        self._storage = storage
        self._celery_app = celery_app

    # Canonical item fields promoted into event.derived_fields before rule
    # evaluation (added 2026-06-27 per architect rule-walk-through finding --
    # see _enrich_event docstring). Listed explicitly rather than auto-promoted
    # via dir() because Pydantic + SimpleNamespace introspection surfaces
    # different non-field attributes (model_config / __dict__ / etc.) and a
    # whitelist makes the promotion contract auditable.
    _PROMOTABLE_ITEM_FIELDS: tuple[str, ...] = (
        # Identity:
        "customer_id", "device_id", "milestone_id", "item_no", "item_id",
        "delivery_item_id", "item_name", "tg_name",
        # State + lifecycle:
        "delivery_state", "prior_delivery_state", "item_completion_pct",
        "actual_completion_date", "last_updated",
        # Type + gates per FR-7 / FR-78 / FR-81:
        "item_type", "force_tracking_enabled", "no_customer_upload",
        "milestone_gating", "review_required", "review_status",
        # Counters + outreach timestamps per FR-9 / FR-10 / NFR-21 §5:
        "doc_count", "reminder_count",
        "last_owner_contacted", "last_reminder_triggered_at",
        "last_owner_response_at",
        # 4-field owner identity per [D-080] + [D-086]:
        "owner_corp_usa_email", "owner_corp_email", "owner_corp_id", "owner_name",
        # TG-denormalized per [D-051]:
        "tg_email_group_alias", "tg_owner_name", "tg_path_id",
        "tg_owner_corp_usa_email", "tg_owner_corp_email", "tg_owner_corp_id",
        "ingress_nsd", "folder_routing_enabled",
        # Routing + tracking:
        "ingress_folder", "target_folder", "item_path_id", "tracking_modality",
        # FR-87 / FR-83 / FR-31 sub-1:
        "manual_triage_required", "rules_paused",
        "pm_approval_at", "pm_approval_pm_id",
        # PLM grouping per FR-5 + [D-035]:
        "plm_id",
        # FR-28 OwnerStatusConfirmed + FR-7 doc-count derivation source:
        "doc_count_received",
        # owner_intent_closed_at -- persisted owner "Closed" intent when
        # doc_count_not_reached blocked transition. Promoted so reconcile rule
        # condition can reference it (architect 2026-06-29 race-resolution).
        "owner_intent_closed_at",
    )

    @staticmethod
    def _derive_facts(item_snapshot: Any) -> dict[str, Any]:
        """Compute conditional facts not present as direct item fields.

        Added 2026-06-27 per architect rule-walk-through Section 4 finding:
        rules like `advance_to_document_received_with_parser` (Rule 4-1) and
        `advance_to_owner_closed_on_status_confirmed` (Rule 4-2) reference
        `doc_count_reached` as a condition field, but it isn't stored
        directly -- it's the boolean (doc_count_received >= doc_count) per
        tracker/guards.py:83. Without this derivation, the FR-28 + FR-7
        state-machine rules silently fail to match.

        Mirrors the rule_engine MODULE.md Worked Example 3 design ("caller
        also supplies derived facts"); centralizing here in the dispatcher
        instead of expecting each trigger source to remember which derived
        facts each rule needs.

        Returns the computed dict. Caller is responsible for letting
        caller-supplied derived_fields take precedence.

        Forward-look: days_to_deadline = (Milestone.target_date - today)
        per [D-085] + deadline_evaluator (workflow_engine MODULE.md line 290)
        will join this method once Milestone snapshot fetch is wired.
        """
        if item_snapshot is None:
            return {}
        derived: dict[str, Any] = {}
        received = getattr(item_snapshot, "doc_count_received", None) or 0
        expected = getattr(item_snapshot, "doc_count", None) or 0
        derived["doc_count_reached"] = received >= expected
        return derived

    # Field-delta -> semantic sub_trigger map per [D-118] + defaults.yaml +
    # automation_rules.yaml rules that key off specialized sub_triggers:
    #   - OwnerReassigned matches handle_owner_reassignment (defaults.yaml:7)
    #     -> NotifyNewOwner + StartItemCollection
    #   - DeadlineMoved matches rearm_deadline_on_milestone_target_moved
    #     -> RearmDeadlineProximity
    #   - TagsModified matches propagate_tags_on_modification
    #     -> PropagateTagsToActiveTrackers
    #
    # The SP alert parser emits raw verbs ("added"/"changed"/"deleted") from
    # the email body's "<title> has been (added|changed|deleted)" header
    # line; it does NOT know which YAML sub_triggers exist. Refinement lives
    # here so the rule engine + YAML stay decoupled from the parser's wire
    # format.
    #
    # Priority order matters when multiple semantic fields change in one
    # alert: owner edits dominate (NotifyNewOwner + StartItemCollection is
    # the most disruptive downstream chain and shouldn't be skipped), then
    # deadline, then tags. Multi-semantic refinement (split one event into N)
    # is a Ph-2 consideration; logged as a known limitation in workflow_engine
    # MODULE.md when this lands.
    _OWNER_DELTA_FIELDS = frozenset({
        "owner_corp_email", "owner_corp_usa_email",
        "owner_corp_id", "owner_name", "owner_employee_id",
    })
    _DEADLINE_DELTA_FIELDS = frozenset({"target_date"})
    _TAGS_DELTA_FIELD_PREFIXES = ("tag_", "tags_")
    # Added 2026-06-28 per architect PM-approval design pass:
    # SP UI engineer's PM Approval button atomically writes 3 fields in one SP
    # transaction (delivery_state + pm_approval_at + pm_approval_pm_id per
    # [D-068]). Refine sub_trigger to "PmApproved" so the rule
    # advance_to_ready_for_submission_on_pm_approval matches via sub_trigger
    # rather than field_deltas key-presence guesswork.
    _PM_APPROVAL_DELTA_FIELDS = frozenset({
        "pm_approval_at", "pm_approval_pm_id",
    })

    # TPM-CLOSE-1 (2026-07-28): TPM early-close via SP UI writes
    # delivery_state='Closed' to the SP row (NotStarted->Closed per D-149
    # or Open->Closed per D-146). SP fires CHANGED alert with delivery_state
    # in field_deltas. Refine sub_trigger to 'TpmSpClose' so the
    # corp-side rule mirror_tpm_sp_close_on_delivery_state_closed matches
    # and apply_tpm_sp_close_task fires (mirror to Postgres).
    #
    # Uses a value-based check (delivery_state new value must equal 'Closed'
    # case-insensitive) rather than a key-based check, because
    # delivery_state ALSO appears in field_deltas for other TPM/PM edits
    # (e.g., pm_approval writes RFS). The TpmSpClose refinement must fire
    # ONLY when the SP-authored new value is literally 'Closed'.
    _TPM_SP_CLOSE_TARGET_VALUE = "closed"

    @staticmethod
    def _delta_new_value(deltas: dict, key: str) -> Any:
        """Extract the 'new' half of a field_deltas entry. Tolerant to both
        (prior, new) tuple/list serialization and bare-scalar dispatch shapes."""
        entry = deltas.get(key)
        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            return entry[1]
        return entry

    @classmethod
    def _refine_sub_trigger(cls, event: TriggerEvent) -> TriggerEvent:
        """Map raw 'changed' SP alerts to semantic sub_triggers based on
        which fields were edited. Leaves added/deleted/None untouched and
        returns the event unchanged when no field_deltas were provided.

        Added 2026-06-27 per architect during Step 2 owner-edit debug:
        SpAlertParser emits sub_trigger='changed' for any SP item edit, but
        handle_owner_reassignment (defaults.yaml) wants sub_trigger=
        'OwnerReassigned'. Without this refinement step the rule never
        matches and owner edits produce zero downstream behavior (no audit
        of a matched action, no NotifyNewOwner email, no StartItemCollection).
        """
        # DEBUG-INSTRUMENTATION 2026-06-30 -- remove after PM-approve cascade
        # debugging is complete. See follow-up cleanup commit.
        logger.info(
            "DBG dispatcher._refine_sub_trigger ENTRY corr_id=%s sub_trigger=%s "
            "field_deltas_keys=%s",
            getattr(event, 'correlation_id', '?'),
            event.sub_trigger,
            sorted((event.field_deltas or {}).keys())[:12],
        )
        if event.sub_trigger != "changed":
            logger.info(
                "DBG dispatcher._refine_sub_trigger SKIP non-changed corr_id=%s sub_trigger=%s",
                getattr(event, 'correlation_id', '?'), event.sub_trigger,
            )
            return event
        deltas = event.field_deltas or {}
        if not deltas:
            logger.info(
                "DBG dispatcher._refine_sub_trigger SKIP empty-deltas corr_id=%s",
                getattr(event, 'correlation_id', '?'),
            )
            return event
        delta_keys = set(deltas.keys())

        refined: str | None = None

        # TPM-CLOSE-1 (2026-07-28): TpmSpClose ORDERED FIRST because it
        # inspects the VALUE of delivery_state, and delivery_state also
        # appears in field_deltas for pm_approval writes (RFS). In
        # practice PM approval writes RFS not Closed so this check
        # matches only when the SP-authored delivery_state new value is
        # literally 'Closed' -- covers both the D-149 NotStarted->Closed
        # early-close path and D-146 Open->Closed mid-lifecycle path.
        # Task apply_tpm_sp_close self-validates the value, so even a
        # misfire here would no-op safely at the task layer.
        if "delivery_state" in delta_keys:
            new_value = cls._delta_new_value(deltas, "delivery_state")
            if (
                isinstance(new_value, str)
                and new_value.strip().lower() == cls._TPM_SP_CLOSE_TARGET_VALUE
            ):
                refined = "TpmSpClose"

        if refined is None and delta_keys & cls._PM_APPROVAL_DELTA_FIELDS:
            # Pattern A (SP-authoritative) per architect 2026-06-28: SP UI
            # engineer's button atomically writes 3 fields; HILDA mirrors.
            # PM-approval check ordered SECOND (after TpmSpClose) because
            # it's less specific than the value-based close check but more
            # specific than owner/deadline/tag key-based checks.
            refined = "PmApproved"
        elif refined is None and delta_keys & cls._OWNER_DELTA_FIELDS:
            refined = "OwnerReassigned"
        elif refined is None and delta_keys & cls._DEADLINE_DELTA_FIELDS:
            refined = "DeadlineMoved"
        elif refined is None and any(k.startswith(cls._TAGS_DELTA_FIELD_PREFIXES) for k in delta_keys):
            refined = "TagsModified"

        if refined is None:
            logger.info(
                "DBG dispatcher._refine_sub_trigger NO_MATCH corr_id=%s "
                "delta_keys=%s (none of PM/OWNER/DEADLINE/TAGS sets matched)",
                getattr(event, 'correlation_id', '?'), sorted(delta_keys)[:12],
            )
            return event
        # DEBUG: promoted from logger.debug to logger.info 2026-06-30
        # for PM-approve cascade debugging.
        logger.info(
            "DBG dispatcher._refine_sub_trigger REFINED corr_id=%s '%s' -> '%s' "
            "(field_deltas=%s)",
            getattr(event, 'correlation_id', '?'),
            event.sub_trigger, refined, sorted(delta_keys)[:8],
        )
        from dataclasses import replace
        return replace(event, sub_trigger=refined)

    def _enrich_event(self, event: TriggerEvent, item_snapshot: Any) -> TriggerEvent:
        """Promote item_snapshot fields into event.derived_fields so the
        rule_engine evaluator can resolve item-field conditions like
        force_tracking_enabled / item_type / delivery_state / reminder_count.

        Caller-supplied derived_fields take precedence -- sp_alert_parser's
        body_kvs + routing_key, kickoff_collection_task's kickoff_source,
        and any other authored facts survive promotion intact.

        Added 2026-06-27 per architect rule-walk-through 2026-06-27 finding:
        evaluator at core/src/rule_engine/evaluator.py:39-44 reads conditions
        only from event.derived_fields + event.field_deltas; item_snapshot is
        used only for the rules_paused check. Without this enrichment every
        rule with an item-field condition would silently log RUL-W006 and
        return False -- which silently broke send_initial_outreach_on_collection_
        start, send_first_reminder_on_no_contact, send_second_reminder_on_no_
        contact, escalate_to_messenger_after_2_reminders, and
        escalate_pm_on_deadline_breach.

        Note: days_to_deadline is NOT enriched here -- it requires Milestone.
        target_date snapshot + (today - target_date) computation per [D-085]
        + deadline_evaluator (workflow_engine MODULE.md line 290). Adding that
        is a follow-up; today's enrichment unblocks Rules 1/2a/2b/3a's
        delivery_state + reminder_count + force_tracking_enabled conditions.
        """
        if item_snapshot is None:
            return event
        promoted: dict[str, Any] = {}
        for field_name in self._PROMOTABLE_ITEM_FIELDS:
            val = getattr(item_snapshot, field_name, None)
            if val is not None:
                promoted[field_name] = val
        # Computed facts (doc_count_reached etc.) layered above raw promotion;
        # caller-supplied derived_fields still win below.
        derived = self._derive_facts(item_snapshot)
        existing = dict(event.derived_fields) if event.derived_fields else {}
        # Caller-supplied facts take precedence over item snapshot defaults.
        merged = {**promoted, **derived, **existing}
        # DEBUG-INSTRUMENTATION 2026-06-30
        logger.info(
            "DBG dispatcher._enrich_event MERGED corr_id=%s "
            "promoted_keys=%s derived_keys=%s existing_keys=%s "
            "merged.delivery_state=%s",
            getattr(event, 'correlation_id', '?'),
            sorted(promoted.keys())[:10],
            sorted(derived.keys())[:6],
            sorted(existing.keys())[:10],
            merged.get('delivery_state'),
        )
        from dataclasses import replace
        return replace(event, derived_fields=merged)

    def _fetch_item_snapshot(self, event: TriggerEvent) -> Any:
        """Fetch item snapshot per D12 cascade 2026-06-23. Returns None for
        item-less events (MilestoneAllClosed, CredentialExpired, etc.) or when
        no storage Protocol injected (e.g., tests)."""
        delivery_item_id = getattr(event.entity_ref, "delivery_item_id", None)
        # DEBUG-INSTRUMENTATION 2026-06-30 -- remove after cascade debugging
        if delivery_item_id is None:
            logger.info(
                "DBG dispatcher._fetch_item_snapshot SKIP no-delivery_item_id "
                "corr_id=%s entity_ref=%s",
                getattr(event, 'correlation_id', '?'),
                {k: getattr(event.entity_ref, k, None)
                 for k in ('customer_id', 'device_id', 'milestone_id', 'delivery_item_id')},
            )
            return None
        if self._storage is None:
            logger.info(
                "DBG dispatcher._fetch_item_snapshot SKIP no-storage corr_id=%s id=%s",
                getattr(event, 'correlation_id', '?'), delivery_item_id,
            )
            return None
        try:
            snap = self._storage.get_delivery_item(delivery_item_id)
            logger.info(
                "DBG dispatcher._fetch_item_snapshot RESULT corr_id=%s id=%s "
                "found=%s delivery_state=%s",
                getattr(event, 'correlation_id', '?'), delivery_item_id,
                snap is not None,
                getattr(snap, 'delivery_state', None),
            )
            return snap
        except Exception:
            # Snapshot fetch failure should not block dispatch -- caller already
            # has the event; we just lose the pause check.
            logger.warning("Failed to fetch item snapshot for %s; dispatching without pause check",
                           delivery_item_id)
            return None

    def _build_event_context(self, event: TriggerEvent) -> dict[str, Any]:
        """Construct JSON-serialisable event_context per Key choice 2026-06-10.

        Per D1 cascade 2026-06-23: uses customer_id / device_id (was customer_slug /
        device_slug)."""
        ref = event.entity_ref
        # Architect 2026-06-30: field_deltas added so SP-alert-driven tasks
        # (apply_pm_approval, future Pattern-A mirrors) can read the
        # SP-authored NEW values that triggered the rule. Previously absent
        # from event_context -> apply_pm_approval_task's
        # `event_context.get("field_deltas")` always returned None ->
        # skipped_no_deltas outcome -> state stayed at UnderPMReview despite
        # the rule firing + task spawning. This was the silent drop point
        # blocking the PM-approve cascade for the entire debug arc.
        return {
            "correlation_id":   event.correlation_id,
            "customer_id":      ref.customer_id,
            "device_id":        getattr(ref, "device_id", None),
            "milestone_id":     getattr(ref, "milestone_id", None),
            "delivery_item_id": getattr(ref, "delivery_item_id", None),
            "trigger":          event.trigger.value,
            "sub_trigger":      event.sub_trigger,
            "timestamp":        event.timestamp.isoformat() if event.timestamp else None,
            # derived_fields passed through 2026-06-27 per [D-118] Chunk 3 -- task
            # bodies can consult caller-supplied facts (e.g. sp_alert_parser puts
            # body_kvs + routing_key here for import_deliverable_tracker_task).
            # None when event has no derived_fields (most internal events).
            "derived_fields":   dict(event.derived_fields) if event.derived_fields else None,
            # field_deltas passed through 2026-06-30 per architect cascade
            # debug -- Pattern-A mirror tasks need the SP-authored NEW values.
            "field_deltas":     dict(event.field_deltas) if event.field_deltas else None,
        }

    def dispatch(self, event: TriggerEvent) -> DispatchResult:
        """Pipeline (Ph-1 per D2 + D12 cascade 2026-06-23):
        1. Fetch item snapshot (None for item-less events).
        2. rule_engine.evaluate(event, item_snapshot=item) -> list[RuleMatch].
        3. For each RuleMatch:
           (a) If pause_state='paused', log WFL-W001 + skip (do not enqueue).
           (b) Else: build_chain_from_rule_match + apply_async; record task ID.
        4. Return DispatchResult.

        Item-less triggers (MilestoneAllClosed, CredentialExpired, etc.) pass
        item_snapshot=None to rule_engine; pause check is skipped automatically.
        """
        # DEBUG-INSTRUMENTATION 2026-06-30
        logger.info(
            "DBG dispatcher.dispatch ENTRY corr_id=%s trigger=%s sub_trigger=%s "
            "entity_ref={customer_id=%s device_id=%s milestone_id=%s delivery_item_id=%s} "
            "field_deltas_keys=%s",
            getattr(event, 'correlation_id', '?'),
            getattr(event.trigger, 'value', event.trigger),
            event.sub_trigger,
            getattr(event.entity_ref, 'customer_id', None),
            getattr(event.entity_ref, 'device_id', None),
            getattr(event.entity_ref, 'milestone_id', None),
            getattr(event.entity_ref, 'delivery_item_id', None),
            sorted((event.field_deltas or {}).keys())[:12],
        )
        # Refine 'changed' sub_trigger to semantic variants (OwnerReassigned /
        # DeadlineMoved / TagsModified) based on which fields were edited, so
        # rules keyed off specialized sub_triggers can match raw SP alerts.
        # Runs first so item-snapshot enrichment below sees the final sub_trigger
        # (some Ph-2 enrichments may key off it).
        event = self._refine_sub_trigger(event)
        item_snapshot = self._fetch_item_snapshot(event)
        # Enrich derived_fields with item snapshot before rule evaluation
        # (2026-06-27 cold-start enrichment per architect rule-walk-through).
        event = self._enrich_event(event, item_snapshot)
        matches: list[RuleMatch] = self._rule_engine.evaluate(event, item_snapshot=item_snapshot)
        # DEBUG-INSTRUMENTATION 2026-06-30
        logger.info(
            "DBG dispatcher.dispatch RULE_ENGINE_RESULT corr_id=%s sub_trigger=%s "
            "matched_count=%d rule_ids=%s",
            getattr(event, 'correlation_id', '?'),
            event.sub_trigger,
            len(matches),
            [m.rule_id for m in matches],
        )

        event_context = self._build_event_context(event)
        scheduled: list[str] = []
        skipped: list[tuple[str, Literal["paused"]]] = []

        for match in matches:
            if match.pause_state == "paused":
                logger.warning("WFL-W001: " + format_code(
                    "WFL-W001", rule_id=match.rule_id,
                    item_id=event_context.get("delivery_item_id") or "<none>",
                ))
                skipped.append((match.rule_id, "paused"))
                continue
            try:
                # DEBUG-INSTRUMENTATION 2026-06-30
                logger.info(
                    "DBG dispatcher.dispatch DISPATCHING rule_id=%s actions=%s corr_id=%s",
                    match.rule_id,
                    [str(a.kind) for a in match.actions],
                    getattr(event, 'correlation_id', '?'),
                )
                signature = build_chain_from_rule_match(match, event_context)
                async_result = signature.apply_async()
                scheduled.append(async_result.id)
                logger.info(
                    "DBG dispatcher.dispatch SCHEDULED rule_id=%s task_id=%s corr_id=%s",
                    match.rule_id, async_result.id,
                    getattr(event, 'correlation_id', '?'),
                )
            except Exception as exc:
                # Per workflow_engine MODULE.md Invariant: dispatch failure is
                # surfaced via WFL-* but does not abort other RuleMatches.
                logger.error("Dispatch failure for RuleMatch %s: %s", match.rule_id, exc)
                # Continue to next match.

        return DispatchResult(
            correlation_id=event.correlation_id,
            matched_count=len(matches),
            scheduled_tasks=scheduled,
            skipped_matches=skipped,
        )
