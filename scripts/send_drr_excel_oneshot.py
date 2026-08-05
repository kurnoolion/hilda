"""One-shot DRR Excel send — bypasses the tpm_notification beat tick entirely.

Use when the tick's send path is blocked (window off, TPM SP lookup issue,
Celery event-loop context bugs, idempotency-audit lockout) but you want to
SEE the actual DRR Excel deliverable land in the TPM's mailbox for review.

Reads work items from Postgres via the sync wrapper (proven to work),
builds the Excel via the same builder the tick uses, renders the same HTML
body, and sends via the same email_sender. Skips: audit rows, idempotency
checks, TPM SP lookup, day-of/day-before window classification, state
transition of item_no=<final_deliverable_item_name>.

Run inside the hilda-worker container (needs the same env + config):

  podman exec -it hilda-worker python -m scripts.send_drr_excel_oneshot \\
      --customer MMK --device SM-S671U1 --milestone DRR \\
      --to t.arasu@samsung.com --tpm-name "Thendral Arasu"

Optional overrides:
  --phase day_of       stamped into subject line + filename (default: day_of)
  --dry-run            build everything, print sizes, DO NOT send

Exit non-zero on send failure so it composes cleanly in shell pipelines.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--customer",  required=True, help="customer_id (e.g. MMK)")
    p.add_argument("--device",    required=True, help="device_id (e.g. SM-S671U1)")
    p.add_argument("--milestone", required=True, help="milestone_id (e.g. DRR)")
    p.add_argument("--to",        required=True, help="TPM email (hardcoded, bypasses SP lookup)")
    p.add_argument("--tpm-name",  default="TPM", help="TPM display name for greeting")
    p.add_argument("--phase",     default="day_of", choices=["day_of", "day_before"])
    p.add_argument("--tz",        default="America/New_York")
    p.add_argument("--dry-run",   action="store_true", help="build only, do not send")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # ---- 1. Read items from Postgres (sync wrapper — proven working) --------
    from core.src.storage.delivery_item_ops import PostgresStorage
    storage = PostgresStorage()
    all_items = storage.list_items_for_milestone(args.milestone) or []
    items = [
        it for it in all_items
        if getattr(it, "customer_id", None) == args.customer
        and getattr(it, "device_id", None) == args.device
    ]
    if not items:
        print(f"ERROR: no items in Postgres scope customer={args.customer} "
              f"device={args.device} milestone={args.milestone}", file=sys.stderr)
        print(f"       (query returned {len(all_items)} items for milestone={args.milestone} "
              "before customer+device filter)", file=sys.stderr)
        return 2
    print(f"loaded {len(items)} items (from {len(all_items)} total for milestone)")

    # ---- 2. Build summary + body + Excel via tick's own helpers -------------
    from core.src.workflow_engine.tasks.tpm_notification import (
        _build_drr_v2_context,
        _build_subject,
        _compute_summary_and_pending,
        _render_body,
    )
    from core.src.email_service.outbound.drr_report_excel import build_drr_report_excel

    summary, pending_by_tg = _compute_summary_and_pending(items)
    body_html = _render_body(
        tpm_name=args.tpm_name,
        summary=summary,
        pending_by_tg=pending_by_tg,
    )
    # DRR-V2-6: same context builder the tick uses. Needs `deps` so SP
    # reads (milestone + project header fields) work. Falls back to
    # legacy 4-column mode when section_grouping is None.
    from core.src.workflow_engine.task_deps import get_task_deps
    try:
        _deps_for_ctx = get_task_deps()
    except Exception:
        from core.src.workflow_engine.bootstrap import build_task_deps
        _deps_for_ctx = build_task_deps().task_deps
    drr_ctx = _build_drr_v2_context(
        _deps_for_ctx, args.customer, args.device, args.milestone,
    )
    xlsx_bytes = build_drr_report_excel(items=items, **drr_ctx)
    xlsx_filename = f"DRR_{args.customer}_{args.device}_{args.milestone}_final.xlsx"
    subject = _build_subject(args.customer, args.device, args.milestone)

    print(f"summary: open={summary['open_count']} closed={summary['closed_count']} "
          f"completion={summary['completion_percent']}%")
    print(f"pending TGs: {len(pending_by_tg)}")
    print(f"body html: {len(body_html):,} bytes")
    print(f"xlsx:      {len(xlsx_bytes):,} bytes ({xlsx_filename})")
    print(f"subject:   {subject}")
    print(f"to:        {args.to}")

    if args.dry_run:
        # Persist artifacts so you can inspect what would have gone out.
        out_dir = Path(f"/tmp/drr_oneshot_{args.customer}_{args.device}_{args.milestone}")
        out_dir.mkdir(exist_ok=True, parents=True)
        (out_dir / "body.html").write_text(body_html, encoding="utf-8")
        (out_dir / xlsx_filename).write_bytes(xlsx_bytes)
        print(f"\nDRY RUN — artifacts written to {out_dir}/")
        return 0

    # ---- 3. Send via the same EmailSender the tick uses ---------------------
    # Reuse deps already loaded in step 2 (no need to bootstrap twice).
    deps = _deps_for_ctx
    if deps.email_sender is None:
        print("ERROR: email_sender not wired in task_deps; cannot send.",
              file=sys.stderr)
        return 3

    # Send is async in the adapter; bridge sync.
    coro = deps.email_sender.send(
        to=[args.to],
        cc=[],
        subject=subject,
        body=body_html,
        attachments=[(
            xlsx_filename,
            xlsx_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )],
    )
    try:
        loop = asyncio.new_event_loop()
        try:
            message_id = loop.run_until_complete(coro)
        finally:
            loop.close()
    except Exception as exc:
        print(f"ERROR: send failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    print(f"\nSENT — message_id={message_id}")
    print(f"       to={args.to} subject={subject!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
