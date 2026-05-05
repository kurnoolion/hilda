"""QC template — fixed-field schema enforcing no-free-text invariant. Anchors [D-002]."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.src.diagnostics.report import ReportRecord, ReportType

_ALLOWED_FIELD_TYPES = {"int", "float", "bool", "enum"}


@dataclass(frozen=True)
class QCField:
    name: str
    field_type: Literal["int", "float", "bool", "enum"]
    enum_values: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.field_type not in _ALLOWED_FIELD_TYPES:
            raise TypeError(
                f"QCField '{self.name}': field_type must be one of "
                f"{sorted(_ALLOWED_FIELD_TYPES)}, got {self.field_type!r}"
            )
        if self.field_type == "enum" and not self.enum_values:
            raise TypeError(
                f"QCField '{self.name}': enum_values required when field_type='enum'"
            )
        if self.field_type != "enum" and self.enum_values is not None:
            raise TypeError(
                f"QCField '{self.name}': enum_values only valid when field_type='enum'"
            )


@dataclass(frozen=True)
class QCTemplate:
    module_prefix: str
    artifact_type: str
    fields: tuple[QCField, ...]

    @property
    def key(self) -> str:
        return f"{self.module_prefix}:{self.artifact_type}"

    def validate_record(self, record: ReportRecord) -> list[str]:
        errors: list[str] = []
        if record.report_type != ReportType.QC:
            errors.append(
                f"expected QC record, got {record.report_type.value}"
            )
        if record.module_prefix != self.module_prefix:
            errors.append(
                f"prefix mismatch: template={self.module_prefix} record={record.module_prefix}"
            )
        for f in self.fields:
            if f.name not in record.fields:
                errors.append(f"missing field '{f.name}'")
                continue
            v = record.fields[f.name]
            if f.field_type == "int":
                if isinstance(v, bool) or not isinstance(v, int):
                    errors.append(f"field '{f.name}': expected int, got {type(v).__name__}")
            elif f.field_type == "float":
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    errors.append(f"field '{f.name}': expected float, got {type(v).__name__}")
            elif f.field_type == "bool":
                if not isinstance(v, bool):
                    errors.append(f"field '{f.name}': expected bool, got {type(v).__name__}")
            elif f.field_type == "enum":
                if not isinstance(v, str):
                    errors.append(f"field '{f.name}': expected enum str, got {type(v).__name__}")
                elif f.enum_values and v not in f.enum_values:
                    errors.append(
                        f"field '{f.name}': value {v!r} not in {list(f.enum_values)}"
                    )
        return errors

    def sample_line(self) -> str:
        rendered = []
        for f in self.fields:
            if f.field_type == "int":
                rendered.append(f"{f.name}=0")
            elif f.field_type == "float":
                rendered.append(f"{f.name}=0.0")
            elif f.field_type == "bool":
                rendered.append(f"{f.name}=false")
            elif f.field_type == "enum":
                first = (f.enum_values or ("?",))[0]
                rendered.append(f"{f.name}={first}")
        body = "|".join(rendered)
        return f"QC|{self.module_prefix}|<run-id>|<timestamp>|{body}"


QC_REGISTRY: dict[str, QCTemplate] = {}


def register_template(template: QCTemplate) -> None:
    """Idempotent for identical re-registration; raises on conflict."""
    existing = QC_REGISTRY.get(template.key)
    if existing is not None and existing != template:
        raise ValueError(
            f"QCTemplate {template.key} already registered with different definition"
        )
    QC_REGISTRY[template.key] = template
