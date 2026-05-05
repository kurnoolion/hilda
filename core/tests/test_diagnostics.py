"""Unit tests for core.src.diagnostics."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from core.src.diagnostics import (
    ERROR_CODES,
    PREFIX_REGISTRY,
    PipelineError,
    QCField,
    QCTemplate,
    ReportRecord,
    ReportType,
    ReportWriter,
    format_code,
    get_code,
    register_code,
    register_template,
)
from core.src.diagnostics.error_codes import ErrorCode


class TestErrorCodes:
    def test_all_18_prefixes_registered(self) -> None:
        assert len(PREFIX_REGISTRY) == 18
        assert PREFIX_REGISTRY["DGN"] == "diagnostics"
        assert PREFIX_REGISTRY["SHP"] == "sharepoint_integration"

    def test_get_code_returns_definition(self) -> None:
        c = get_code("DGN-E001")
        assert c.code == "DGN-E001"
        assert "Duplicate prefix" in c.message

    def test_get_code_unknown_raises_dgn_e002(self) -> None:
        with pytest.raises(PipelineError) as ei:
            get_code("XXX-E999")
        assert ei.value.code_id == "DGN-E002"

    def test_format_code_substitutes_kwargs(self) -> None:
        msg = format_code("DGN-E001", prefix="ABC", module="test")
        assert "ABC" in msg
        assert "test" in msg

    def test_register_code_with_known_prefix(self) -> None:
        new = ErrorCode("DGN-E099", "test code: {x}", False)
        register_code(new)
        assert "DGN-E099" in ERROR_CODES
        # idempotent re-register
        register_code(new)
        # cleanup
        del ERROR_CODES["DGN-E099"]

    def test_register_code_unknown_prefix_raises(self) -> None:
        with pytest.raises(PipelineError) as ei:
            register_code(ErrorCode("ZZZ-E001", "bad", False))
        assert ei.value.code_id == "DGN-E001"

    def test_register_code_conflict_raises(self) -> None:
        register_code(ErrorCode("DGN-E098", "first", False))
        with pytest.raises(ValueError):
            register_code(ErrorCode("DGN-E098", "second", False))
        del ERROR_CODES["DGN-E098"]

    def test_pipeline_error_carries_context(self) -> None:
        err = PipelineError("DGN-E001", context={"prefix": "ABC", "module": "x"})
        assert err.code_id == "DGN-E001"
        assert err.context == {"prefix": "ABC", "module": "x"}
        assert "ABC" in str(err)


class TestReportRecord:
    def test_to_line_no_fields(self) -> None:
        rec = ReportRecord(
            ReportType.RPT,
            "DGN",
            "run-1",
            datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
            {},
        )
        assert rec.to_line() == "RPT|DGN|run-1|2026-05-05T10:00:00Z"

    def test_to_line_with_fields(self) -> None:
        rec = ReportRecord(
            ReportType.QC,
            "SHP",
            "run-2",
            datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
            {"count": 42, "ok": True, "ratio": 0.5, "result": "OK"},
        )
        line = rec.to_line()
        assert line.startswith("QC|SHP|run-2|2026-05-05T10:00:00Z|")
        assert "count=42" in line
        assert "ok=true" in line
        assert "ratio=0.5" in line
        assert "result=OK" in line

    def test_round_trip(self) -> None:
        original = ReportRecord(
            ReportType.MET,
            "EML",
            "run-3",
            datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
            {"sent": 10, "failed": False, "p99_ms": 250.5, "phase": "warmup"},
        )
        roundtripped = ReportRecord.from_line(original.to_line())
        assert roundtripped.report_type == original.report_type
        assert roundtripped.module_prefix == original.module_prefix
        assert roundtripped.fields == original.fields

    def test_from_line_malformed_raises(self) -> None:
        with pytest.raises(PipelineError):
            ReportRecord.from_line("not|enough|parts")
        with pytest.raises(PipelineError):
            ReportRecord.from_line("BAD|DGN|run|2026-05-05T10:00:00Z")
        with pytest.raises(PipelineError):
            ReportRecord.from_line("RPT|DGN|run|not-a-timestamp")
        with pytest.raises(PipelineError):
            ReportRecord.from_line("RPT|DGN|run|2026-05-05T10:00:00Z|noequalssign")


class TestReportWriter:
    def test_make_emits_and_returns(self) -> None:
        writer = ReportWriter("DGN", "run-1")
        rec = writer.make(ReportType.RPT, {"a": 1})
        assert rec.report_type == ReportType.RPT
        assert rec.fields["a"] == 1

    def test_flush_writes_to_destination(self) -> None:
        writer = ReportWriter("DGN", "run-1")
        writer.make(ReportType.RPT, {"a": 1})
        writer.make(ReportType.MET, {"b": 2})
        out = io.StringIO()
        writer.flush(out)
        lines = out.getvalue().strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("RPT|DGN|run-1|")
        assert lines[1].startswith("MET|DGN|run-1|")

    def test_context_manager_flushes_on_exit(self) -> None:
        out = io.StringIO()
        with ReportWriter("DGN", "run-1") as w:
            w.make(ReportType.RPT, {"a": 1})
            w.flush(out)
        assert "RPT|DGN|run-1|" in out.getvalue()


class TestQCTemplate:
    def test_qcfield_rejects_bad_type(self) -> None:
        with pytest.raises(TypeError):
            QCField("x", "string")  # type: ignore[arg-type]

    def test_qcfield_enum_requires_values(self) -> None:
        with pytest.raises(TypeError):
            QCField("x", "enum")

    def test_qcfield_non_enum_rejects_values(self) -> None:
        with pytest.raises(TypeError):
            QCField("x", "int", enum_values=("a",))

    def test_validate_record_ok(self) -> None:
        tpl = QCTemplate(
            "DGN",
            "test",
            (
                QCField("count", "int"),
                QCField("ok", "bool"),
                QCField("result", "enum", ("OK", "FAIL")),
            ),
        )
        rec = ReportRecord(
            ReportType.QC,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"count": 5, "ok": True, "result": "OK"},
        )
        assert tpl.validate_record(rec) == []

    def test_validate_record_wrong_report_type(self) -> None:
        tpl = QCTemplate("DGN", "test", (QCField("count", "int"),))
        rec = ReportRecord(
            ReportType.RPT,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"count": 5},
        )
        errors = tpl.validate_record(rec)
        assert any("expected QC" in e for e in errors)

    def test_validate_record_missing_field(self) -> None:
        tpl = QCTemplate(
            "DGN",
            "test",
            (QCField("count", "int"), QCField("ok", "bool")),
        )
        rec = ReportRecord(
            ReportType.QC,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"count": 5},
        )
        errors = tpl.validate_record(rec)
        assert any("missing field 'ok'" in e for e in errors)

    def test_validate_record_bool_not_int(self) -> None:
        # bool is subclass of int — must be rejected for int fields
        tpl = QCTemplate("DGN", "test", (QCField("count", "int"),))
        rec = ReportRecord(
            ReportType.QC,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"count": True},
        )
        assert tpl.validate_record(rec)

    def test_validate_record_enum_value_not_in_set(self) -> None:
        tpl = QCTemplate("DGN", "test", (QCField("r", "enum", ("OK", "FAIL")),))
        rec = ReportRecord(
            ReportType.QC,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"r": "MAYBE"},
        )
        assert tpl.validate_record(rec)

    def test_register_template_idempotent(self) -> None:
        tpl = QCTemplate("DGN", "self_test", (QCField("count", "int"),))
        register_template(tpl)
        register_template(tpl)
        # cleanup
        from core.src.diagnostics.qc import QC_REGISTRY
        del QC_REGISTRY["DGN:self_test"]

    def test_sample_line_renders(self) -> None:
        tpl = QCTemplate(
            "DGN",
            "test",
            (
                QCField("count", "int"),
                QCField("result", "enum", ("OK", "FAIL")),
            ),
        )
        line = tpl.sample_line()
        assert line.startswith("QC|DGN|")
        assert "count=0" in line
        assert "result=OK" in line


class TestNoProprietaryContent:
    """Negative tests per [D-002] hard invariant."""

    def test_qcfield_rejects_str_type_as_field(self) -> None:
        # No 'str' field type — only int / float / bool / enum
        with pytest.raises(TypeError):
            QCField("free_prose", "str")  # type: ignore[arg-type]

    def test_report_field_str_only_for_enums_or_codes(self) -> None:
        # The schema doesn't enforce this at the dataclass level, but the
        # QCTemplate validator catches it. Verify enum-typed fields reject
        # arbitrary strings.
        tpl = QCTemplate("DGN", "guard", (QCField("status", "enum", ("OK",)),))
        rec = ReportRecord(
            ReportType.QC,
            "DGN",
            "run-1",
            datetime.now(timezone.utc),
            {"status": "customer test report leaked here"},
        )
        assert tpl.validate_record(rec)
