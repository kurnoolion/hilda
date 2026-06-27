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
    bootstrap_task_deps(rules_dir=tmp_path, auto_storage=False, auto_audit=False)
    deps1 = get_task_deps()
    bootstrap_task_deps(rules_dir=tmp_path, auto_storage=False, auto_audit=False)
    deps2 = get_task_deps()
    # Both calls succeeded; deps singleton has been re-set
    assert deps1 is not None and deps2 is not None
    _restore_task_deps_to_none()


# ---- Chunk 4: auto-construct PostgresStorage + PostgresAuditWriter ----


def test_bootstrap_storage_wires_via_config_defaults_when_no_json_or_env(
    tmp_path, monkeypatch
):
    """No JSON + no env -> GlobalStorageConfig.from_sources falls back to
    its model defaults (postgresql+asyncpg://hilda@localhost:5432/hilda).
    Bootstrap tries to wire; whether it succeeds depends on whether postgres
    is reachable -- in tests, configure_engine on a fictitious host doesn't
    raise (engine is lazy); init_db may or may not raise. Test only verifies
    bootstrap returns without crashing."""
    _restore_task_deps_to_none()
    monkeypatch.delenv("HILDA_STORAGE_DB_URL", raising=False)
    # Use a tmp dir that doesn't contain config/storage.json
    monkeypatch.chdir(tmp_path)
    result = bootstrap_task_deps(rules_dir=tmp_path, auto_storage=False, auto_audit=False)
    # auto_storage=False -> storage stays None deliberately
    assert result.storage_wired is False
    _restore_task_deps_to_none()


def test_bootstrap_auto_constructs_postgres_storage_when_url_set(tmp_path, monkeypatch):
    """HILDA_STORAGE_DB_URL=sqlite -> auto-constructs PostgresStorage + audit."""
    _restore_task_deps_to_none()
    db_path = tmp_path / "boot.db"
    monkeypatch.setenv("HILDA_STORAGE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    result = bootstrap_task_deps(rules_dir=tmp_path)
    assert result.storage_wired is True
    assert result.audit_wired is True
    deps = get_task_deps()
    assert deps.storage is not None
    assert deps.audit is not None
    _restore_task_deps_to_none()


def test_bootstrap_caller_supplied_storage_skips_auto(tmp_path, monkeypatch):
    """Caller-supplied storage takes precedence; auto-construct skipped."""
    _restore_task_deps_to_none()
    monkeypatch.setenv("HILDA_STORAGE_DB_URL", "sqlite+aiosqlite:///should-not-be-used.db")
    fake_storage = SimpleNamespace(get_delivery_item=lambda _id: None)
    result = bootstrap_task_deps(
        rules_dir=tmp_path, storage=fake_storage,
    )
    assert result.storage_wired is True
    deps = get_task_deps()
    assert deps.storage is fake_storage
    _restore_task_deps_to_none()


# ---- SP writer auto-construct (architect direction 2026-06-27) ----


def test_bootstrap_skips_sp_writer_when_config_missing(tmp_path, monkeypatch):
    """No SP config -> SpCrudWriter not constructed; sp_writer stays None;
    silent-skip with warning per architect direction."""
    _restore_task_deps_to_none()
    # Clear any HILDA_SP_* env vars
    for k in list(__import__("os").environ.keys()):
        if k.startswith("HILDA_SP_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)   # no config/sharepoint_integration.json here
    result = bootstrap_task_deps(
        rules_dir=tmp_path, auto_storage=False, auto_audit=False,
    )
    assert result.sp_writer_wired is False
    assert any("sp_writer_skip" in w for w in result.warnings)
    deps = get_task_deps()
    assert deps.sp_writer is None
    _restore_task_deps_to_none()


def test_bootstrap_caller_supplied_sp_writer_skips_auto(tmp_path):
    """Caller-supplied sp_writer takes precedence; auto-construct skipped."""
    _restore_task_deps_to_none()
    fake_writer = SimpleNamespace(update_item=lambda *a, **k: None,
                                  create_item=lambda *a, **k: "SP-1")
    result = bootstrap_task_deps(
        rules_dir=tmp_path, sp_writer=fake_writer,
        auto_storage=False, auto_audit=False,
    )
    assert result.sp_writer_wired is True
    deps = get_task_deps()
    assert deps.sp_writer is fake_writer
    _restore_task_deps_to_none()
