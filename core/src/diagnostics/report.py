"""Compact report types: RPT / MET / FIX / QC. Anchors [D-002] NFR-17."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TextIO

from core.src.diagnostics.error_codes import PipelineError


class ReportType(str, Enum):
    RPT = "RPT"
    MET = "MET"
    FIX = "FIX"
    QC = "QC"


_FieldValue = int | float | bool | str


@dataclass
class ReportRecord:
    report_type: ReportType
    module_prefix: str
    run_id: str
    timestamp: datetime
    fields: dict[str, _FieldValue] = field(default_factory=dict)

    def to_line(self) -> str:
        ts = self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        head = f"{self.report_type.value}|{self.module_prefix}|{self.run_id}|{ts}"
        tail = "|".join(f"{k}={_serialize(v)}" for k, v in self.fields.items())
        return f"{head}|{tail}" if tail else head

    @classmethod
    def from_line(cls, line: str) -> "ReportRecord":
        parts = line.strip().split("|")
        if len(parts) < 4:
            raise PipelineError(
                "DGN-E002", context={"code": f"<malformed report line: {line!r}>"}
            )
        try:
            report_type = ReportType(parts[0])
        except ValueError as e:
            raise PipelineError(
                "DGN-E002", context={"code": f"<bad report type: {parts[0]}>"}
            ) from e
        try:
            ts = datetime.strptime(parts[3], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError as e:
            raise PipelineError(
                "DGN-E002", context={"code": f"<bad timestamp: {parts[3]}>"}
            ) from e
        fields: dict[str, _FieldValue] = {}
        for part in parts[4:]:
            if "=" not in part:
                raise PipelineError(
                    "DGN-E002", context={"code": f"<bad field: {part!r}>"}
                )
            k, v = part.split("=", 1)
            fields[k] = _deserialize(v)
        return cls(
            report_type=report_type,
            module_prefix=parts[1],
            run_id=parts[2],
            timestamp=ts,
            fields=fields,
        )


def _serialize(v: _FieldValue) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _deserialize(v: str) -> _FieldValue:
    if v == "true":
        return True
    if v == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


class ReportWriter:
    """Collects ReportRecords and flushes to a stream."""

    def __init__(self, module_prefix: str, run_id: str) -> None:
        self.module_prefix = module_prefix
        self.run_id = run_id
        self._buffer: list[ReportRecord] = []

    def emit(self, record: ReportRecord) -> None:
        self._buffer.append(record)

    def make(
        self,
        report_type: ReportType,
        fields: dict[str, _FieldValue] | None = None,
    ) -> ReportRecord:
        rec = ReportRecord(
            report_type=report_type,
            module_prefix=self.module_prefix,
            run_id=self.run_id,
            timestamp=datetime.now(timezone.utc),
            fields=dict(fields or {}),
        )
        self.emit(rec)
        return rec

    def flush(self, dest: TextIO | None = None) -> None:
        target = dest if dest is not None else sys.stdout
        for rec in self._buffer:
            target.write(rec.to_line() + "\n")
        target.flush()
        self._buffer.clear()

    def __enter__(self) -> "ReportWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.flush()
