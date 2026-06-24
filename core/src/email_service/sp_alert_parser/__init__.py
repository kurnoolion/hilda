"""sp_alert_parser -- parses SP alert emails per [D-047] + FR-84 + FR-87.

The ONLY SP -> HILDA channel per FR-84 (firewall blocks SP -> HILDA HTTP
unconditionally). Module #11 architect Q1 lock 2026-06-25 narrowed scope to
3 lists: Milestones_<customer_id>, Projects_<customer_id>, Deliverables_<customer_id>.
"""
from core.src.email_service.sp_alert_parser.parser import (
    ALERT_SCOPE_LISTS,
    AlertRoutingKey,
    ParsedSpAlert,
    SpAlertParser,
)
from core.src.email_service.sp_alert_parser.routing_key import extract_routing_key

__all__ = [
    "ALERT_SCOPE_LISTS",
    "AlertRoutingKey",
    "ParsedSpAlert",
    "SpAlertParser",
    "extract_routing_key",
]
