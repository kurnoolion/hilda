"""HILDA's Celery application instance.

Configured per [D-022] + [D-043] (Redis broker Ph-1/Ph-2; Postgres result backend).
Queue topology = 4 queues by latency profile per architect lock 2026-06-10:
- `default`: fast outreach / SP writes / notifications (4-8 workers per host)
- `llm_calls`: FR-53 LLM document review + FR-12 path c message classification +
  FR-52 step 4 ROUTE_ATTACHMENT + FR-85 CLASSIFY_DOC_TYPE (1-2 workers, dedicated
  to avoid blocking default queue; minutes-scale per [D-052])
- `browser_automation`: customer_adapter.upload_attachment (10-100x slower than
  REST per [D-054]; 1 worker per host per carrier session pool)
- `periodic`: beat-triggered polling tickers (short-lived; fires TriggerEvents
  onto default queue then exits)

Signal handlers emit WFL-* compact-report lines per [D-002]; no proprietary
content; only task name + queue + latency bucket + error code.
"""
from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.signals import (
    task_failure, task_postrun, task_prerun, task_retry, worker_init,
)

from core.src.diagnostics import format_code

from .config import WorkflowEngineConfig

__all__ = ["hilda_celery_app", "build_celery_app"]

logger = logging.getLogger(__name__)


def build_celery_app(config: WorkflowEngineConfig | None = None) -> Celery:
    """Build a Celery app from a WorkflowEngineConfig. Default singleton constructed
    at import time below as `hilda_celery_app`; tests may construct their own via
    this function with overrides."""
    cfg = config or WorkflowEngineConfig.from_sources()

    app = Celery(
        "hilda",
        broker=cfg.broker_url,
        backend=cfg.result_backend_url,
        # task module imports are done lazily by the worker entry points to avoid
        # circular-import issues at __init__ time; tests stub via task_routes.
        include=[],
    )

    # Queue routing -- see MODULE.md ## Public surface celery_app.py.
    app.conf.task_routes = {
        "core.src.workflow_engine.tasks.review.trigger_ai_review":    {"queue": "llm_calls"},
        "core.src.workflow_engine.tasks.submission.queue_submission": {"queue": "browser_automation"},
        "core.src.workflow_engine.polling.*":                          {"queue": "periodic"},
        "*":                                                           {"queue": "default"},
    }

    # Default retry policy applied at @task decorator time; values exposed here for
    # task modules to import and use consistently.
    app.conf.task_acks_late = True
    app.conf.task_reject_on_worker_lost = True
    app.conf.task_default_max_retries = cfg.task_default_max_retries
    app.conf.task_default_retry_delay = 30           # base 30s; combined with jitter
    app.conf.broker_connection_retry_on_startup = True
    app.conf.task_track_started = True
    # Allow tests to run tasks eagerly without a real broker.
    app.conf.task_always_eager = False

    # Beat schedule -- periodic tasks fired by hilda-beat. Added 2026-06-27
    # per architect direction (no automatic ews_receiver poll prior to this).
    # Module imports happen lazily so tests that don't need beat don't pay
    # the cost.
    app.conf.beat_schedule = {
        "poll_ews_inbox_60s": {
            "task":     "core.src.workflow_engine.tasks.email_polling.poll_ews_inbox",
            "schedule": 60.0,    # seconds; matches email_service.json ews.poll_interval_s default
            "options":  {"queue": "default", "expires": 55},
            # expires=55 -> if beat fires while previous poll still running,
            # the newer one expires before reaching worker (avoids backlog).
        },
        # Reconciliation meta-reconciler per [D-142]/[D-143]. Single beat entry
        # dispatches 5 sync sub-tasks serially per (customer x device x milestone)
        # tuple. Interval default 300s (5 min) tunable via config/reconcile.json
        # or HILDA_RECONCILE_INTERVAL_SEC env. expires=290 avoids beat backlog.
        # Missed-SP-email backstop; task naturally no-ops per tick when drift
        # conditions don't apply (see reconcile_config.py + strand
        # reconcile-sync-cascade for design rationale).
        "reconcile_all_300s": {
            "task":     "core.src.workflow_engine.tasks.reconcile.reconcile_all",
            "schedule": 300.0,
            "options":  {"queue": "default", "expires": 290},
        },
        # TPM DRR closure notification tick per architect 2026-07-15 Phase C.
        # Fires every 300s by default; task body computes US/Eastern now,
        # walks Milestones per customer, and sends the DRR closure email at
        # 00:00 Eastern on target_date-1 and target_date. Send window +
        # strict-only + ops-alert-on-miss all tunable via
        # config/tpm_notification.json. Task no-ops when config.enabled=false.
        # expires=290 avoids beat backlog if a tick takes longer than the
        # interval (unlikely -- most ticks read a few SP rows and finish
        # in seconds).
        "tpm_notification_tick_300s": {
            "task":     "core.src.workflow_engine.tasks.tpm_notification.tick",
            "schedule": 300.0,
            "options":  {"queue": "default", "expires": 290},
        },
        # SETUP-1 (2026-07-28) — TPM setup-complete notification per architect.
        # Fires every 60s (tunable via TpmNotificationConfig.setup_complete_
        # beat_interval_seconds); task no-ops when config.setup_complete_enabled
        # is False. Task body scans all (customer, device, milestone) scopes
        # with delivery_items in Postgres; sends one email per scope when
        # every item is past 'Not Started' state (all D-144 auto-transitions
        # settled) + no prior 'setup_complete_notified' audit exists.
        # Idempotent per scope (option A per architect 2026-07-28).
        "setup_complete_notification_tick_60s": {
            "task":     "core.src.workflow_engine.tasks.setup_complete_notification.tick",
            "schedule": 60.0,
            "options":  {"queue": "default", "expires": 55},
        },
        # UR-8 (Ph-2 2026-08-01) — weekly ops digest of unrouted files (the
        # _unknownTG bucket). Aggregates counts across every scope with
        # document_index rows and emails ops. Interval hard-coded here at 7d;
        # runtime cadence + recipient + min-count threshold + kill switch all
        # live on TpmNotificationConfig (ops_unrouted_digest_*). Empty
        # recipient short-circuits the tick.
        "ops_unrouted_digest_weekly": {
            "task":     "core.src.workflow_engine.tasks.ops_digest.ops_unrouted_digest_tick",
            "schedule": 604800.0,
            "options":  {"queue": "default", "expires": 3600},
        },
    }
    return app


# Singleton app for production import paths. Tests construct their own via
# build_celery_app(WorkflowEngineConfig(...)) or set task_always_eager=True.
hilda_celery_app: Celery = build_celery_app()


# ---------------------------------------------------------------------------
# Signal handlers -- emit WFL-* lines for compact-report observability.
# ---------------------------------------------------------------------------


@task_prerun.connect
def _on_task_prerun(sender: Any = None, task_id: str | None = None, **kwargs: Any) -> None:
    name = sender.name if sender is not None else "<unknown>"
    logger.debug("WFL prerun task=%s id=%s", name, task_id)


@task_postrun.connect
def _on_task_postrun(sender: Any = None, task_id: str | None = None,
                      state: str | None = None, **kwargs: Any) -> None:
    name = sender.name if sender is not None else "<unknown>"
    logger.debug("WFL postrun task=%s id=%s state=%s", name, task_id, state)


@task_retry.connect
def _on_task_retry(sender: Any = None, request: Any = None, reason: Any = None, **kwargs: Any) -> None:
    name = sender.name if sender is not None else "<unknown>"
    attempt = getattr(request, "retries", 0) + 1 if request is not None else 1
    max_retries = getattr(sender, "max_retries", "?") if sender is not None else "?"
    item_id = "?"
    exc_class = type(reason).__name__ if reason is not None else "Unknown"
    logger.warning("WFL-W003: " + format_code(
        "WFL-W003", task_name=name, n=attempt, max=max_retries,
        item_id=item_id, exc_class=exc_class,
    ))


@task_failure.connect
def _on_task_failure(sender: Any = None, task_id: str | None = None,
                      exception: Any = None, **kwargs: Any) -> None:
    name = sender.name if sender is not None else "<unknown>"
    exc_class = type(exception).__name__ if exception is not None else "Unknown"
    retries = getattr(sender, "request", None)
    retry_count = getattr(retries, "retries", "?") if retries is not None else "?"
    item_id = "?"   # task body extracts from event_context when available
    logger.error("WFL-E005: " + format_code(
        "WFL-E005", task_name=name, retries=retry_count, item_id=item_id, exc_class=exc_class,
    ))


# ---------------------------------------------------------------------------
# Worker-init bootstrap -- TaskDeps wireup at worker startup
# (added 2026-06-27 per architect direction during rule-walk-through:
# dispatcher + email_sender wired so kickoff_collection_task ItemCreated chain
# + outreach email send are functional in production).
# ---------------------------------------------------------------------------


@worker_init.connect
def _on_worker_init(**kwargs: Any) -> None:
    """Build + install TaskDeps before any task body runs.

    Best-effort: missing config / credentials / concrete impls don't crash
    worker startup. Each piece logs its skip reason via BootstrapResult so
    the architect can see what's wired vs stub.

    Test isolation: this signal fires only when a real Celery worker boots
    (via `celery worker`). In-process tests that use task_always_eager don't
    trigger worker_init; tests inject TaskDeps directly via override_task_deps.
    """
    try:
        from core.src.workflow_engine.bootstrap import bootstrap_task_deps
        bootstrap_task_deps()
    except Exception as exc:  # noqa: BLE001 -- bootstrap is best-effort
        logger.error("WFL-BOOTSTRAP-FAILED: %s: %s", type(exc).__name__, str(exc)[:200])
