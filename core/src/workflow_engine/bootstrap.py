"""Worker startup bootstrap -- constructs TaskDeps + injects via set_task_deps.

Added 2026-06-27 per architect direction during rule-walk-through 2026-06-27
("dispatcher wired into TaskDeps at worker startup; email_sender wired into
TaskDeps at worker startup"). Wires the pieces that today's Ph-1 outreach
end-to-end test needs:

  1. RuleEngine -- loaded from customizations/rules/global/*.yaml so the rule
     ladder is in memory when tasks fire
  2. TriggerDispatcher -- so kickoff_collection_task.dispatcher.dispatch works
     (Chunk 4 of [D-118] cascade)
  3. EmailSender -- so send_initial_outreach / send_reminder / notify_new_owner
     produce real emails (vs audit-only)

Best-effort: missing config / credentials / storage Protocol impl don't crash
the worker. Each piece is wrapped in try/except; on failure the slot stays
None and the corresponding task body degrades gracefully (audit-only,
skipped_no_dispatcher, etc.). The bootstrap result + which slots got wired
is logged at startup so the architect can see what's live vs stub.

Production storage / sp_writer / audit / customer_adapter / messenger
implementations land in a follow-up commit; today's bootstrap leaves those
slots wired-or-None per whichever modules already expose builders.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.src.workflow_engine.task_deps import TaskDeps, set_task_deps

__all__ = ["bootstrap_task_deps", "BootstrapResult"]

_log = logging.getLogger(__name__)


class BootstrapResult:
    """Captures what got wired vs what didn't. Returned by bootstrap_task_deps
    so worker startup can log a summary + tests can assert."""

    def __init__(self) -> None:
        self.storage_wired: bool = False
        self.sp_writer_wired: bool = False
        self.audit_wired: bool = False
        self.dispatcher_wired: bool = False
        self.email_sender_wired: bool = False
        self.customer_adapter_wired: bool = False
        self.messenger_wired: bool = False
        self.rule_engine_wired: bool = False
        self.warnings: list[str] = []

    def summary_line(self) -> str:
        bits = [
            ("storage",   self.storage_wired),
            ("sp_writer", self.sp_writer_wired),
            ("audit",     self.audit_wired),
            ("dispatcher",   self.dispatcher_wired),
            ("email_sender", self.email_sender_wired),
            ("customer_adapter", self.customer_adapter_wired),
            ("messenger", self.messenger_wired),
            ("rule_engine",  self.rule_engine_wired),
        ]
        wired = [name for name, ok in bits if ok]
        skipped = [name for name, ok in bits if not ok]
        return (
            f"task_deps bootstrap: wired={wired or '[]'} "
            f"skipped={skipped or '[]'} warnings={len(self.warnings)}"
        )


def bootstrap_task_deps(
    *,
    rules_dir: Path | None = None,
    storage: Any = None,
    sp_writer: Any = None,
    audit: Any = None,
    customer_adapter: Any = None,
    messenger: Any = None,
) -> BootstrapResult:
    """Construct the TaskDeps bundle for production worker startup.

    Optional args let callers (tests, special-case deployments) inject
    pre-built dependencies; remaining slots are constructed by this function
    via best-effort discovery.

    rules_dir: directory containing global rule YAMLs. Defaults to
        customizations/rules/global/ relative to repo root.
    storage / sp_writer / audit / customer_adapter / messenger: optional
        pre-built dependencies. None means "best-effort discovery" (today's
        Ph-1 = leaves None if no builder is wired yet).

    Returns BootstrapResult for observability + tests.
    """
    result = BootstrapResult()
    result.storage_wired = storage is not None
    result.sp_writer_wired = sp_writer is not None
    result.audit_wired = audit is not None
    result.customer_adapter_wired = customer_adapter is not None
    result.messenger_wired = messenger is not None

    # -------- 1. RuleEngine from YAML rules directory --------
    rule_engine = _build_rule_engine(rules_dir, result)

    # -------- 2. TriggerDispatcher --------
    dispatcher = _build_dispatcher(rule_engine, storage, result)

    # -------- 3. EmailSender --------
    email_sender = _build_email_sender(result)

    # -------- 4. Install --------
    deps = TaskDeps(
        storage=storage,
        sp_writer=sp_writer,
        audit=audit,
        email_sender=email_sender,
        messenger=messenger,
        customer_adapter=customer_adapter,
        dispatcher=dispatcher,
    )
    set_task_deps(deps)
    _log.info(result.summary_line())
    return result


# ---------------------------------------------------------------------------
# Builders -- each catches its own exceptions + records skip reason
# ---------------------------------------------------------------------------


def _build_rule_engine(rules_dir: Path | None, result: BootstrapResult) -> Any:
    try:
        from core.src.rule_engine import RuleEngine
        from core.src.rule_engine.loader import load_rule_set

        if rules_dir is None:
            # Default location per rule_engine MODULE.md D7 cascade:
            # Ph-1 reads customizations/rules/global/*.yaml only.
            rules_dir = Path("customizations/rules/global")

        if not rules_dir.is_dir():
            result.warnings.append(f"rule_engine_skip: rules_dir={rules_dir} not a directory")
            return None

        rule_set = load_rule_set(rules_dir)
        engine = RuleEngine(rule_set)
        result.rule_engine_wired = True
        return engine
    except Exception as exc:  # noqa: BLE001 -- bootstrap is best-effort
        result.warnings.append(f"rule_engine_skip: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _build_dispatcher(rule_engine: Any, storage: Any, result: BootstrapResult) -> Any:
    if rule_engine is None:
        result.warnings.append("dispatcher_skip: rule_engine not wired")
        return None
    try:
        from core.src.workflow_engine.dispatcher import TriggerDispatcher
        dispatcher = TriggerDispatcher(rule_engine=rule_engine, storage=storage)
        result.dispatcher_wired = True
        return dispatcher
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"dispatcher_skip: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _build_email_sender(result: BootstrapResult) -> Any:
    try:
        from core.src.email_service import build_sender
        from core.src.email_service.config import EmailServiceConfig
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"email_sender_skip_import: {type(exc).__name__}")
        return None

    try:
        cfg = EmailServiceConfig.from_sources()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 -- missing config is a soft-skip
        result.warnings.append(f"email_sender_skip_config: {type(exc).__name__}")
        return None

    # Credential service: optional. If module exposes one, use it; else None.
    credential_service: Any = None
    try:
        from core.src.credential_service import build_credential_service  # type: ignore
        credential_service = build_credential_service()
    except Exception:  # noqa: BLE001 -- credential service is optional in dev
        result.warnings.append("email_sender_no_credential_service: build_credential_service not importable")

    try:
        sender = build_sender(cfg, credential_service)
        result.email_sender_wired = True
        return sender
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"email_sender_skip_build: {type(exc).__name__}: {str(exc)[:120]}")
        return None
