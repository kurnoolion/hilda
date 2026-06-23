"""workflow_engine operational config — 3-tier precedence per [D-025] + [D-038].

CLI overrides > env vars (HILDA_WORKFLOW_ENGINE_*) > config/workflow_engine.json > defaults.
Mirrors rule_engine.config and storage.config patterns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["WorkflowEngineConfig"]

_DEFAULT_CONFIG_PATH = Path("config/workflow_engine.json")

_ENV_MAP = {
    "broker_url":                       "HILDA_WORKFLOW_ENGINE_BROKER_URL",
    "result_backend_url":               "HILDA_WORKFLOW_ENGINE_RESULT_BACKEND_URL",
    "worker_concurrency_default":       "HILDA_WORKFLOW_ENGINE_WORKER_CONCURRENCY_DEFAULT",
    "worker_concurrency_llm":           "HILDA_WORKFLOW_ENGINE_WORKER_CONCURRENCY_LLM",
    "worker_concurrency_browser":       "HILDA_WORKFLOW_ENGINE_WORKER_CONCURRENCY_BROWSER",
    "beat_default_interval_s":          "HILDA_WORKFLOW_ENGINE_BEAT_DEFAULT_INTERVAL_S",
    "owner_reply_poll_interval_s":      "HILDA_WORKFLOW_ENGINE_OWNER_REPLY_POLL_INTERVAL_S",
    "task_default_max_retries":         "HILDA_WORKFLOW_ENGINE_TASK_DEFAULT_MAX_RETRIES",
    "task_default_retry_backoff_max_s": "HILDA_WORKFLOW_ENGINE_TASK_DEFAULT_RETRY_BACKOFF_MAX_S",
}


class WorkflowEngineConfig(BaseModel):
    """Operational values per workflow_engine/MODULE.md. Per Ph-1 D3 + D4 cascade
    2026-06-23: no Postgres-override consumption fields (deferred to Ph-2)."""

    model_config = ConfigDict(extra="forbid")

    broker_url:                       str = "redis://hilda-redis:6379/0"
    result_backend_url:               str = "db+postgresql://hilda:hilda@hilda-postgres:5432/hilda"
    worker_concurrency_default:       int = 4
    worker_concurrency_llm:           int = 2
    worker_concurrency_browser:       int = 1
    beat_default_interval_s:          int = 300   # deadline-evaluator tick (5 min default)
    owner_reply_poll_interval_s:      int = 30    # FR-23 third-tier fallback
    task_default_max_retries:         int = 5
    task_default_retry_backoff_max_s: int = 300

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "WorkflowEngineConfig":
        """3-tier precedence: CLI overrides > env vars > config file > defaults."""
        data: dict[str, object] = {}
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        for field_name, env_key in _ENV_MAP.items():
            if env_key in os.environ:
                data[field_name] = os.environ[env_key]
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                data[key] = value
        return cls(**data)
