"""corp_plm sub-module — CorpPlmAdapter thin wrapper + poller + in-flight tracker
+ on_prem_client (PLM-1 2026-08-14).

Per Module #13 cascade D2 2026-06-25. See core/src/issue_tracker/MODULE.md.
"""
from core.src.issue_tracker.corp_plm import on_prem_client
from core.src.issue_tracker.corp_plm.adapter import CorpPlmAdapterBase
from core.src.issue_tracker.corp_plm.in_flight_tracker import InFlightDownloadTracker
from core.src.issue_tracker.corp_plm.poller import CorpPlmPoller, PollingTier

__all__ = [
    "CorpPlmAdapterBase",
    "InFlightDownloadTracker",
    "CorpPlmPoller",
    "PollingTier",
    "on_prem_client",
]
