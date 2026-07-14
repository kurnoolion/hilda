"""DRR closure final-status Excel report builder per architect 2026-07-15.

Consumed by workflow_engine.tasks.tpm_notification when the beat task fires
the TPM notification (2 emails per milestone: target_date - 1 and target_date
at 00:00 US Eastern). Excel is attached alongside the HTML body summary
tables.

Sheet layout (single sheet, matches architect screenshot):
    | Item No | Item Title | Open/Closed Status | Owner Comment |

Status column:
    "Closed" -- delivery_state in {Closed, ReadyForSubmission, SubmittedToCustomer}
                (per architect 2026-07-15: "closed here means either state=closed
                or state=ReadyForSubmission" -- extended to SubmittedToCustomer
                for parity since that state is also post-approval).
    "Open"   -- everything else (Not Started, Open, OutreachSent, DocumentReceived,
                UnderPMReview, Delayed, Blocked, OwnerClosed).

openpyxl >=3.1 was already declared in requirements.txt (Excel spec
ingest era, task #S1). No new dependencies.
"""
from __future__ import annotations

import io
from typing import Any, Iterable

__all__ = ["build_drr_report_excel", "CLOSED_LIKE_STATES", "status_for_item"]


# Delivery states that count as "Closed" in the DRR closure report per architect
# 2026-07-15. Everything else counts as "Open" in the aggregate + Excel status.
CLOSED_LIKE_STATES: frozenset[str] = frozenset({
    "Closed",
    "ReadyForSubmission",
    "SubmittedToCustomer",
})


def status_for_item(item: Any) -> str:
    """Return 'Closed' or 'Open' per the DRR report convention."""
    state = getattr(item, "delivery_state", None) or ""
    return "Closed" if state in CLOSED_LIKE_STATES else "Open"


def build_drr_report_excel(items: Iterable[Any]) -> bytes:
    """Return the DRR closure final-status Excel bytes.

    Args:
        items: iterable of DeliveryItemBase-like objects with attrs
               item_no, item_name (or item_title), delivery_state,
               owner_status_note. Confirmation / Default items are
               included as-is (caller decides scope).

    Returns:
        bytes containing the .xlsx file, safe to attach to an email.
    """
    # Lazy import: openpyxl is a heavy dep; only load when we actually build.
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "DRR Closure"

    # Header row -- friendly labels matching outreach_table.j2 convention.
    headers = ("Item No", "Item Title", "Open/Closed Status", "Owner Comment")
    header_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
    header_font = Font(bold=True)
    for col_idx, label in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")

    # Data rows.
    materialized = list(items or [])
    # Sort by item_no ascending for TPM readability (kickoff already does this,
    # but callers may pass any order).
    materialized.sort(key=lambda it: (getattr(it, "item_no", 0) or 0))
    for row_offset, it in enumerate(materialized, start=2):
        item_no = getattr(it, "item_no", None)
        item_name = (
            getattr(it, "item_name", None)
            or getattr(it, "item_title", None)
            or f"Item {item_no or '?'}"
        )
        status = status_for_item(it)
        note = getattr(it, "owner_status_note", None) or ""
        ws.cell(row=row_offset, column=1, value=item_no)
        ws.cell(row=row_offset, column=2, value=str(item_name))
        ws.cell(row=row_offset, column=3, value=status)
        ws.cell(row=row_offset, column=4, value=str(note))

    # Column widths -- tuned for readability without measuring content.
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 42
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 60

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
