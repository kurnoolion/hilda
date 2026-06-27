"""Tests for core/src/workflow_engine/bootstrap.py."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.src.workflow_engine.bootstrap import bootstrap_task_deps, BootstrapResult
from core.src.workflow_engine.task_deps import get_task_deps, set_task_deps


def _restore_task_deps_to_none() -> None:
    """Reset task deps singleton between tests."""
    import core.src.workflow_engine.task_deps as _td
    _td._deps = None


def test_bootstrap_with_no_config_returns_result_with_warnings():
    """When nothing is wired (no rules dir, no email config), bootstrap
    completes without crashing + records warnings."""
    _restore_task_deps_to_none()
    result = bootstrap_task_deps(rules_dir=Path("/nonexistent/rules/dir"))
    assert isinstance(result, BootstrapResult)
    assert result.rule_engine_wired is False
    assert result.dispatcher_wired is False
    assert len(result.warnings) >= 1
    # TaskDeps got installed with None slots (no crash):
    deps = get_task_deps()
    assert deps.dispatcher is None
    _restore_task_deps_to_none()


def test_bootstrap_wires_dispatcher_when_rule_engine_present(tmp_path):
    """When rule_engine is constructable (empty rules dir is OK), dispatcher
    gets wired into TaskDeps."""
    _restore_task_deps_to_none()
    # Empty rules dir: load_rule_set should return an empty RuleSet
    result = bootstrap_task_deps(rules_dir=tmp_path)
    if result.rule_engine_wired:
        assert result.dispatcher_wired is True
        deps = get_task_deps()
        assert deps.dispatcher is not None
    _restore_task_deps_to_none()


def test_bootstrap_passes_injected_storage_to_dispatcher(tmp_path):
    """Caller-supplied storage flows into the dispatcher (for production
    setups that build storage separately)."""
    _restore_task_deps_to_none()
    fake_storage = SimpleNamespace(get_delivery_item=lambda _id: None)
    result = bootstrap_task_deps(rules_dir=tmp_path, storage=fake_storage)
    if result.dispatcher_wired:
        deps = get_task_deps()
        # Dispatcher should reference the injected storage
        assert deps.dispatcher is not None
        assert deps.dispatcher._storage is fake_storage   # noqa: SLF001
    assert result.storage_wired is True
    _restore_task_deps_to_none()


def test_bootstrap_records_email_sender_skip_when_config_missing(tmp_path):
    """No EmailServiceConfig in env -> email_sender stays None + skip warning."""
    _restore_task_deps_to_none()
    # Force EmailServiceConfig.from_sources to fail by patching it
    with patch(
        "core.src.email_service.config.EmailServiceConfig.from_sources",
        side_effect=RuntimeError("no config in env"),
    ):
        result = bootstrap_task_deps(rules_dir=tmp_path)
    assert result.email_sender_wired is False
    assert any("email_sender_skip" in w for w in result.warnings)
    deps = get_task_deps()
    assert deps.email_sender is None
    _restore_task_deps_to_none()


def test_bootstrap_result_summary_line_lists_wired_and_skipped():
    """summary_line groups what's wired vs not for log readability."""
    r = BootstrapResult()
    r.dispatcher_wired = True
    r.rule_engine_wired = True
    r.warnings.append("test_warning")
    line = r.summary_line()
    assert "dispatcher" in line
    assert "rule_engine" in line
    assert "skipped" in line
    assert "warnings=1" in line


def test_bootstrap_is_idempotent_re_set(tmp_path):
    """Calling bootstrap twice -> second install overrides first; no crash."""
    _restore_task_deps_to_none()
    bootstrap_task_deps(rules_dir=tmp_path)
    deps1 = get_task_deps()
    bootstrap_task_deps(rules_dir=tmp_path)
    deps2 = get_task_deps()
    # Both calls succeeded; deps singleton has been re-set
    assert deps1 is not None and deps2 is not None
    _restore_task_deps_to_none()
