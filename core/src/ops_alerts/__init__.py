"""ops_alerts module -- HILDA OPS alert mechanism.

Per [D-127] Ratified 2026-06-26. Single ingress for HILDA-internal
anomaly/failure signals; fans out to ONE email (HILDA OPS BOT alias) +
N messenger DMs (recipients.yaml broadcast_corp_ids).

Public surface:
- `OpsAlerts` Protocol + `Severity` enum + `OpsAlertResult` dataclass
- `OpsAlertsConfig` composition-root config
- `OpsAlertsService` concrete impl
- `Recipients` + `load_recipients` (recipients.yaml loader)
- `MockOpsAlerts` (tests + --diagnostic)
- `compose_alert` (pure composer for tests + reuse)
- `build_ops_alerts` (composition helper)

Wiring: caller modules import this Protocol, NOT a concrete class.
"""
from core.src.ops_alerts.composer import ComposedAlert, compose_alert
from core.src.ops_alerts.config import OpsAlertsConfig
from core.src.ops_alerts.mocks import MockOpsAlerts
from core.src.ops_alerts.protocol import OpsAlertResult, OpsAlerts, Severity
from core.src.ops_alerts.rate_limiter import RateLimiter
from core.src.ops_alerts.recipients_loader import Recipients, load_recipients
from core.src.ops_alerts.service import OpsAlertsService, build_ops_alerts

__all__ = [
    # Protocol surface
    "OpsAlerts",
    "Severity",
    "OpsAlertResult",
    # Config
    "OpsAlertsConfig",
    # Loader + dataclass
    "Recipients",
    "load_recipients",
    # Composer
    "ComposedAlert",
    "compose_alert",
    # Rate limiter
    "RateLimiter",
    # Service + composition helper
    "OpsAlertsService",
    "build_ops_alerts",
    # Test double
    "MockOpsAlerts",
]
