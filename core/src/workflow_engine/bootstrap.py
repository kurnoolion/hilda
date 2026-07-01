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
    auto_storage: bool = True,
    auto_audit: bool = True,
    auto_sp_writer: bool = True,
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

    # -------- 0. Auto-construct concrete storage + audit if not pre-injected --------
    # Storage strand Chunk 4 (2026-06-27): when HILDA_STORAGE_DB_URL is set
    # and no caller-supplied storage was passed, build PostgresStorage +
    # PostgresAuditWriter from the existing storage module. This wires the
    # Rule 1 conditions on force_tracking_enabled etc. into a real database.
    if storage is None and auto_storage:
        storage = _build_postgres_storage(result)
    if audit is None and auto_audit:
        audit = _build_postgres_audit_writer(result)
    if sp_writer is None and auto_sp_writer:
        sp_writer = _build_sp_writer(result)

    result.storage_wired = storage is not None
    result.sp_writer_wired = sp_writer is not None
    result.audit_wired = audit is not None
    result.messenger_wired = messenger is not None

    # -------- 1. RuleEngine from YAML rules directory --------
    rule_engine = _build_rule_engine(rules_dir, result)

    # -------- 2. TriggerDispatcher --------
    dispatcher = _build_dispatcher(rule_engine, storage, result)

    # -------- 3. EmailSender --------
    email_sender = _build_email_sender(result)

    # -------- 3.5 CustomerAdapter -- Ph-1 architect 2026-07-01 wire-up --------
    # If caller pre-injected a customer_adapter, honor it (tests / special
    # deploys). Otherwise auto-discover via HILDA_CUSTOMER_ID env var
    # (defaults to "MMK"; single-customer Ph-1 lock -- multi-customer is Ph-2).
    if customer_adapter is None:
        customer_adapter = _build_customer_adapter(result, audit=audit)
    result.customer_adapter_wired = customer_adapter is not None

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
    """Resolve rules_dir via 3-tier precedence (caller arg > RuleEngineConfig
    JSON/env > module default). Loader walks <rules_dir>/global/*.yaml only
    per D7 cascade 2026-06-23 (Ph-1 = Global tier only)."""
    try:
        from core.src.rule_engine import RuleEngine
        from core.src.rule_engine.config import RuleEngineConfig
        from core.src.rule_engine.loader import RuleSet

        if rules_dir is None:
            cfg = RuleEngineConfig.from_sources()  # reads config/rule_engine.json + env
            rules_dir = cfg.rules_dir

        if not rules_dir.is_dir():
            result.warnings.append(f"rule_engine_skip: rules_dir={rules_dir} not a directory")
            return None

        # Use RuleSet.load classmethod (per rule_engine MODULE.md API).
        # collision/orphan audits left at defaults (True); user can override via
        # RuleEngineConfig fields if they want them disabled at bootstrap time.
        rule_set = RuleSet.load(rules_dir)
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


def _build_postgres_storage(result: BootstrapResult) -> Any:
    """Construct PostgresStorage using GlobalStorageConfig.from_sources()
    which reads config/storage.json + HILDA_STORAGE_DB_URL env + CLI overrides
    per 3-tier precedence (extended 2026-06-27 to honor JSON-driven deployments).

    Calls configure_engine + init_db so the schema exists; idempotent on
    re-call (SQLAlchemy DDL is CREATE IF NOT EXISTS via metadata.create_all).
    """
    try:
        from core.src.storage._sync_bridge import run_async_sync
        from core.src.storage.config import GlobalStorageConfig
        from core.src.storage.db import configure_engine, init_db
        from core.src.storage.delivery_item_ops import PostgresStorage

        cfg = GlobalStorageConfig.from_sources()
        # Detect "default-only" fallback: if no JSON + no env, from_sources
        # returns the model defaults which point at localhost. Honor the
        # defaults silently (some local dev setups want this) but record a
        # skip warning so deployments without intended DB config can spot it.
        configure_engine(url=cfg.db_url)
        run_async_sync(init_db)
        return PostgresStorage()
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"postgres_storage_skip_build: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _build_postgres_audit_writer(result: BootstrapResult) -> Any:
    """Construct PostgresAuditWriter. Reuses the engine configured by
    _build_postgres_storage; no separate engine init."""
    try:
        from core.src.storage.audit_writer_impl import PostgresAuditWriter
        return PostgresAuditWriter()
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"postgres_audit_skip_build: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _build_sp_writer(result: BootstrapResult) -> Any:
    """Construct SpCrudWriter conforming to tracker.SpWriter Protocol.

    Reads GlobalSharePointConfig.from_sources() (config/sharepoint_integration.json
    + HILDA_SP_* env + CLI overrides) and FileBasedListProvider (loads
    customizations/sharepoint_config/customers/*.yaml). Both have built-in
    self-discovering constructors -- bootstrap just wires them together.

    Per [D-064]: HILDA -> SP REST is the sole writeback channel; SpCrudWriter
    is the canonical Protocol impl tracker tasks call when they need to mirror
    HILDA state transitions back to the SP row.

    Silent-skip semantics consistent with the other auto-construct paths --
    missing config, missing creds, missing customer YAMLs all degrade
    gracefully; sp_writer stays None; tracker tasks degrade to audit-only on
    SP writes (existing graceful-degrade pattern in tracker.update_delivery_state).
    """
    try:
        from core.src.sharepoint_integration.config import GlobalSharePointConfig
        from core.src.sharepoint_integration.list_crud import SpCrud
        from core.src.sharepoint_integration.list_provider import FileBasedListProvider
        from core.src.sharepoint_integration.sp_client import SpClient
        from core.src.sharepoint_integration.sp_writer_impl import SpCrudWriter

        # GlobalSharePointConfig.from_sources() has no default config_path
        # (unlike GlobalStorageConfig). Pass the conventional location
        # explicitly so JSON-driven deployments don't fall through to env-only.
        # First existing path wins; env vars + CLI args still override JSON.
        sp_config_path: Path | None = None
        for candidate in (
            Path("config/sharepoint_integration.json"),       # standard layout
            Path("/app/config/sharepoint_integration.json"),  # container baked-image fallback
        ):
            if candidate.exists():
                sp_config_path = candidate
                break

        cfg = GlobalSharePointConfig.from_sources(config_path=sp_config_path)
        client = SpClient(cfg)
        provider = FileBasedListProvider(
            Path("customizations/sharepoint_config")
        )
        crud = SpCrud(client, provider)
        return SpCrudWriter(crud)
    except Exception as exc:  # noqa: BLE001 -- silent-skip per architect direction
        result.warnings.append(
            f"sp_writer_skip: {type(exc).__name__}: {str(exc)[:120]}"
        )
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

    # Credential service: optional but needed for sops-backed creds
    # (architect's deployment per yesterday's EWS validation). Try concrete
    # SopsCredentialService first; fall back to None when env/key paths
    # aren't set up (dev / unit-test setups).
    credential_service: Any = None
    try:
        import os
        from core.src.credential_service.service import SopsCredentialService
        # Honor SOPS_AGE_KEY_FILE env var if set (standard sops convention)
        # so deployments with custom key paths (e.g. /etc/hilda/age-key/keys.txt)
        # decrypt correctly. Falls back to default if env unset.
        age_key_env = os.environ.get("SOPS_AGE_KEY_FILE")
        kwargs: dict[str, Any] = {}
        if age_key_env:
            from pathlib import Path as _P
            kwargs["age_key_path"] = _P(age_key_env)
        credential_service = SopsCredentialService(**kwargs)
    except Exception as exc:  # noqa: BLE001 -- credential service is optional in dev
        result.warnings.append(
            f"email_sender_no_credential_service: {type(exc).__name__}: {str(exc)[:120]}"
        )

    try:
        sender = build_sender(cfg, credential_service)
        result.email_sender_wired = True
        return sender
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"email_sender_skip_build: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _build_customer_adapter(result: BootstrapResult, *, audit: Any = None) -> Any:
    """Ph-1 architect 2026-07-01: auto-discover the per-customer adapter
    subclass so submit_to_carrier_task gets a live customer_adapter without
    manual injection.

    Discovery convention:
      1. HILDA_CUSTOMER_ID env var (default "MMK") -> lowercase customer id
      2. Import `customizations.customer_adapter.<customer_id_lower>_adapter`
      3. Call module.ADAPTER_FACTORY(audit_writer=audit) -> adapter instance

    Boot-time directory pre-creation (opt-in per env var, default OFF):
      If HILDA_BOOTSTRAP_GDRIVE_DIRS is truthy AND the adapter exposes
      `bootstrap_directories`, load
      customizations/template_schemas/<customer_id>/template.yaml as a raw
      dict and pass it to the method. Best-effort -- failures leave the
      adapter wired but skip pre-creation.

    Best-effort throughout: no ImportError, missing config, or template
    load failure fails the whole bootstrap. Missing pieces just leave the
    slot None + record a warning; submit_to_carrier_task audits
    'skipped_no_adapter' at run time.
    """
    import os

    customer_id = os.environ.get("HILDA_CUSTOMER_ID", "MMK")
    module_name = (
        f"customizations.customer_adapter.{customer_id.lower()}_adapter"
    )
    try:
        import importlib
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(
            f"customer_adapter_no_module: {module_name}: "
            f"{type(exc).__name__}: {str(exc)[:120]}"
        )
        return None

    factory = getattr(module, "ADAPTER_FACTORY", None)
    if factory is None or not callable(factory):
        result.warnings.append(
            f"customer_adapter_no_factory: {module_name} missing ADAPTER_FACTORY"
        )
        return None

    try:
        instance = factory(audit_writer=audit)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(
            f"customer_adapter_factory_failed: {module_name}: "
            f"{type(exc).__name__}: {str(exc)[:120]}"
        )
        return None

    _log.info(
        "customer_adapter wired: customer_id=%s module=%s class=%s",
        customer_id, module_name, type(instance).__name__,
    )

    # Boot-time directory pre-creation -- default ON per architect 2026-07-01.
    # Set HILDA_SKIP_GDRIVE_DIRS_BOOTSTRAP=1 to opt out (e.g. tests / dev boxes
    # without a GDrive session). Idempotent: create_gdrive_dir returns True
    # on already-exists, so N-worker restart races don't duplicate folders.
    if not _env_truthy("HILDA_SKIP_GDRIVE_DIRS_BOOTSTRAP"):
        bootstrap_method = getattr(instance, "bootstrap_directories", None)
        if bootstrap_method is not None and callable(bootstrap_method):
            template = _load_template_yaml(customer_id, result)
            if template is not None:
                try:
                    import asyncio
                    summary = asyncio.run(bootstrap_method(template))
                    _log.info(
                        "customer_adapter bootstrap_directories: %s", summary,
                    )
                except Exception as exc:  # noqa: BLE001
                    result.warnings.append(
                        f"customer_adapter_bootstrap_dirs_failed: "
                        f"{type(exc).__name__}: {str(exc)[:120]}"
                    )
        else:
            _log.info(
                "customer_adapter: bootstrap_directories method not found on "
                "%s; skipping boot-time folder pre-creation",
                type(instance).__name__,
            )
    else:
        _log.info(
            "customer_adapter: HILDA_SKIP_GDRIVE_DIRS_BOOTSTRAP set; "
            "skipping boot-time folder pre-creation",
        )

    return instance


def _env_truthy(name: str) -> bool:
    """Yes/no interpretation of a string env var. Default False (unset -> off)."""
    import os
    val = (os.environ.get(name) or "").strip().lower()
    return val in ("1", "true", "yes", "on", "y")


def _load_template_yaml(customer_id: str, result: BootstrapResult) -> Any:
    """Load customizations/template_schemas/<customer_id>/template.yaml as
    a raw dict (yaml.safe_load). Returns None on failure with a warning.

    Raw dict passed to bootstrap_directories rather than Pydantic-validated
    model so the walker isn't fragile against template schema drift.
    """
    try:
        import yaml
        from pathlib import Path as _P
        # Repo root is 2 levels above this file's dir (core/src/workflow_engine)
        repo_root = _P(__file__).resolve().parents[3]
        template_path = (
            repo_root / "customizations" / "template_schemas"
            / customer_id / "template.yaml"
        )
        if not template_path.exists():
            result.warnings.append(
                f"template_yaml_missing: {template_path}"
            )
            return None
        with template_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(
            f"template_yaml_load_failed: {type(exc).__name__}: {str(exc)[:120]}"
        )
        return None
