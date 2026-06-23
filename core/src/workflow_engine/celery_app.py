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
from celery.signals import task_failure, task_postrun, task_prerun, task_retry

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
