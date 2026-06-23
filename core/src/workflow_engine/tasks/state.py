"""state.py: UPDATE_STATE + INSTANTIATE_DEFAULT_WORK_ITEM task bodies.

Both delegate to `tracker` per workflow_engine MODULE.md sub-modules section.
Thin wrappers that:
- pull runtime deps from task_deps
- call the corresponding tracker function
- return a dict for Celery canvas chaining (next task in chain gets it as
  positional arg via Celery's default behavior)
"""
from __future__ import annotations

from typing import Any

from core.src.rule_engine import ActionKind
from core.src.tracker import (
    DeliveryState,
    instantiate_default_workitem,
    update_delivery_state,
)

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["update_state_task", "instantiate_default_work_item_task"]


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.state.update_state")
def update_state_task(params: dict[str, Any], event_context: dict[str, Any]) -> dict[str, Any]:
    """UPDATE_STATE -> tracker.update_delivery_state.

    params:
      - target_state: str (DeliveryState enum value)
      - bypass_guards: bool (optional; default False; FR-14 manual override path)

    event_context: standard workflow_engine event_context (correlation_id,
    customer_id, milestone_id, delivery_item_id, trigger_source).

    Returns dict suitable for chain-next consumption.
    """
    deps = get_task_deps()
    delivery_item_id = event_context.get("delivery_item_id")
    if delivery_item_id is None:
        raise ValueError("UPDATE_STATE requires delivery_item_id in event_context")

    target_value = params.get("target_state")
    if target_value is None:
        raise ValueError("UPDATE_STATE requires 'target_state' in params")
    target_state = (
        target_value if isinstance(target_value, DeliveryState)
        else DeliveryState(target_value)
    )
    bypass_guards = bool(params.get("bypass_guards", False))

    result = update_delivery_state(
        delivery_item_id=delivery_item_id,
        target_state=target_state,
        params=params,
        event_context=event_context,
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
        bypass_guards=bypass_guards,
    )
    return {
        "outcome":           result.outcome,
        "from_state":        result.from_state.value,
        "to_state":          result.to_state.value,
        "delivery_item_id":  result.item_id,
        "correlation_id":    result.correlation_id,
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.state.instantiate_default_work_item")
def instantiate_default_work_item_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """INSTANTIATE_DEFAULT_WORK_ITEM -> tracker.instantiate_default_workitem.

    params:
      - milestone_id: str
      - customer_id: str
      - device_id: str
      - inferred_tg_path_id: str (optional; default "_unrouted")

    event_context: standard event_context (correlation_id, customer_id, etc.).
    """
    deps = get_task_deps()
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    if milestone_id is None:
        raise ValueError("INSTANTIATE_DEFAULT_WORK_ITEM requires milestone_id")

    customer_id = params.get("customer_id") or event_context.get("customer_id")
    device_id = params.get("device_id") or event_context.get("device_id")
    if customer_id is None or device_id is None:
        raise ValueError("INSTANTIATE_DEFAULT_WORK_ITEM requires customer_id + device_id")

    inferred_tg_path_id = params.get("inferred_tg_path_id", "_unrouted")
    item = instantiate_default_workitem(
        milestone_id=milestone_id,
        customer_id=customer_id,
        device_id=device_id,
        inferred_tg_path_id=inferred_tg_path_id,
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
        event_context=event_context,
    )
    return {
        "delivery_item_id":  getattr(item, "delivery_item_id", None),
        "milestone_id":      milestone_id,
        "outcome":           "instantiated",
    }


register_task_binding(TaskBinding(
    action_kind=ActionKind.UPDATE_STATE,
    celery_task=update_state_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.INSTANTIATE_DEFAULT_WORK_ITEM,
    celery_task=instantiate_default_work_item_task,
    queue="default",
))
