"""CorpPlmPoller -- deadline-tiered polling cadence per FR-26 + FR-23 lock.

Per Module #13 cascade D6 2026-06-25 + Q3 architect direction.

Defaults (Ph-1 per architect; per-customer override via `polling_schedule`
AutomationRule rows in storage):

  >14 days from deadline   ->  60 min   (baseline tier)
  ≤14 days                 ->  30 min
  ≤7  days                 ->  15 min
  ≤3  days                 ->  5  min
  ≤1  day                  ->  1  min   (deadline-day tier)

Per active DeliveryItem with `plm_id` set + `delivery_state` ∈ {Open, OutreachSent,
DocumentReceived, OwnerClosed} (NOT Closed / Cancelled):
  1. Resolve effective interval from days_to_deadline.
  2. Call CorpPlmAdapter.get_documents_list(plm_id, tpm_corp_id).
  3. Diff returned PlmDocumentNode list against persisted seen-set keyed on
     (plm_id, file_id).
  4. For each new (plm_id, file_id): acquire InFlightDownloadTracker; on success,
     call CorpPlmAdapter.download_file(...) to NSD audit path
     <tg>/<item>/plm/<plm_id>/<file_id>/<document_name>.
  5. Emit TriggerEvent (kind=AttachmentReceived) for downstream FR-86 routing.

Note: HILDA-internal seen-set is in-memory Ph-1; Ph-2 will use the
`DocumentIndexRow` persisted table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

from core.src.issue_tracker.corp_plm.in_flight_tracker import InFlightDownloadTracker
from core.src.issue_tracker.protocol import CorpPlmAdapter, PlmDocumentNode


@dataclass(frozen=True)
class PollingTier:
    """One breakpoint in the deadline-tiered cadence ladder.

    `days_before_deadline` is the inclusive upper bound (`days_to_deadline <=
    days_before_deadline` selects this tier); the baseline tier carries
    `days_before_deadline = None` and matches any days_to_deadline.

    `interval_minutes` is the polling interval to apply when this tier matches.
    """

    days_before_deadline: int | None  # None => baseline / always-applicable
    interval_minutes: int


# Default ladder per architect Q3 + FR-23 lock 2026-06-25.
# Ordered from tightest (smallest days_before_deadline) to baseline.
DEFAULT_POLLING_LADDER: tuple[PollingTier, ...] = (
    PollingTier(days_before_deadline=1, interval_minutes=1),
    PollingTier(days_before_deadline=3, interval_minutes=5),
    PollingTier(days_before_deadline=7, interval_minutes=15),
    PollingTier(days_before_deadline=14, interval_minutes=30),
    PollingTier(days_before_deadline=None, interval_minutes=60),
)


def resolve_polling_interval(
    days_to_deadline: int,
    ladder: Iterable[PollingTier] = DEFAULT_POLLING_LADDER,
) -> int:
    """Walks the ladder from tightest to baseline; returns the interval of the
    first tier whose `days_before_deadline >= days_to_deadline` (or baseline).

    Per FR-23 cross-FR cadence consistency lock: "HILDA applies the interval of
    the first threshold where days_to_deadline <= days_before_deadline; baseline
    tier with days_before_deadline: null is always required as the fallback."
    """
    for tier in ladder:
        if tier.days_before_deadline is None:
            return tier.interval_minutes
        if days_to_deadline <= tier.days_before_deadline:
            return tier.interval_minutes
    # Should not reach -- baseline guarantees a match.
    raise ValueError("Polling ladder is missing a baseline (days_before_deadline=None) tier")


@dataclass
class ActivePollTarget:
    """One PLM polling target per (device_id, milestone_name, owner_corp_id) per
    FR-26. Carries the resolved plm_id + tpm_corp_id + days_to_deadline + the NSD
    audit-path components.
    """

    plm_id: str
    tpm_corp_id: str
    days_to_deadline: int
    nsd_root: Path                 # <NSD_root>/internal/<carrier>/<device>/<milestone>/<tg>/<item>/plm
    delivery_item_id: str          # for downstream TriggerEvent emission


class TriggerEmitter(Protocol):
    """Subset of `workflow_engine` trigger interface this poller depends on.

    Decouples from concrete impl (matches the AuditWriter convention in
    `customer_adapter`). Concrete impl: `workflow_engine.TriggerDispatcher` per
    `[D-113]`. Optional injection -- if None passed, emission is skipped.
    """

    def emit_attachment_received(
        self,
        delivery_item_id: str,
        plm_id: str,
        file_id: str,
        downloaded_path: Path,
    ) -> None: ...


@dataclass
class CorpPlmPoller:
    """Per FR-26 deadline-tiered cadence loop.

    Stateful: maintains an in-memory seen-set keyed on (plm_id, file_id) Ph-1;
    Ph-2 will read from persisted `DocumentIndexRow`.
    """

    adapter: CorpPlmAdapter
    in_flight: InFlightDownloadTracker
    trigger_emitter: TriggerEmitter | None = None
    ladder: tuple[PollingTier, ...] = DEFAULT_POLLING_LADDER
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def interval_for(self, days_to_deadline: int) -> int:
        """Returns the polling interval (minutes) for the given days_to_deadline."""
        return resolve_polling_interval(days_to_deadline, self.ladder)

    async def poll_target(self, target: ActivePollTarget) -> list[Path]:
        """Runs one poll cycle against one target. Returns the list of NSD paths
        where NEW files were written (skipped in-flight files are NOT included).

        Steps:
          (a) get_documents_list(plm_id, tpm_corp_id)
          (b) diff returned nodes against the in-memory seen-set
          (c) for each new (plm_id, file_id):
              - acquire in-flight tracker
              - on acquired: download_file -> nsd_path; add to seen-set on success
              - on skip: log ITR-W003 (caller's logger; this method is pure)
          (d) emit TriggerEvent(AttachmentReceived) for each successful download
        """
        downloaded_paths: list[Path] = []
        nodes = await self.adapter.get_documents_list(target.plm_id, target.tpm_corp_id)
        for node in nodes:
            key = (target.plm_id, node.file_id)
            if key in self._seen:
                continue
            async with self.in_flight.acquire(key) as acquired:
                if not acquired:
                    # ITR-W003 -- in-flight skip; caller logs.
                    continue
                # Build NSD audit path: <nsd_root>/<plm_id>/<file_id>/<document_name>
                dest_dir = target.nsd_root / target.plm_id / node.file_id
                dest_path = dest_dir / node.document_name
                ok = await self.adapter.download_file(
                    target.tpm_corp_id,
                    node.document_id,
                    node.document_name,
                    node.file_id,
                    dest_path,
                )
                if ok:
                    self._seen.add(key)
                    downloaded_paths.append(dest_path)
                    if self.trigger_emitter is not None:
                        self.trigger_emitter.emit_attachment_received(
                            target.delivery_item_id,
                            target.plm_id,
                            node.file_id,
                            dest_path,
                        )
        return downloaded_paths

    def reset_seen(self) -> None:
        """Clears the in-memory seen-set. Test hook; Ph-2 will be unnecessary
        once DocumentIndexRow-backed."""
        self._seen.clear()
