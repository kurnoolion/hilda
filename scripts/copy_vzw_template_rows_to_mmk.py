#!/usr/bin/env python3
"""copy_vzw_template_rows_to_mmk.py -- restore staging's MMK template rows
from prod's VZW template rows.

Reads every row in `Deliverables_Template` where carrier == "VZW" and
milestone_name in {DRR, P1}, clones each one with carrier rewritten to
"MMK", and POSTs the clones back to the same list. All other fields
(tg_name, item_type, tracking_modality, doc_count, item_description,
etc.) are copied verbatim.

Idempotent: before creating a clone, checks whether a row with the
same (carrier=MMK, milestone_name, item_no) already exists in the
list. If so, skips (no duplicates, no update).

Config via env vars:
  HILDA_SP_SITE       e.g. https://spserver/sites/MNOCENTRAL
  HILDA_SP_USER       corp domain user (DOMAIN\\username)
  HILDA_SP_PASS       corp domain password
  TEMPLATE_LIST_NAME  optional, defaults to "Deliverables_Template"
  MILESTONES          optional, comma-separated, defaults to "DRR,P1"

Usage:
  python copy_vzw_template_rows_to_mmk.py --dry-run    # preview only
  python copy_vzw_template_rows_to_mmk.py              # actually write

Notes / caveats:
  * If the list has Person/User fields (e.g. TPM), those come back as
    a lookup Id in the source row. The clone POSTs the same Id, which
    is correct as long as the person still exists in the site's
    user info list. If you see 400s citing a UserField, comment out
    the copy of that specific field.
  * If the list has Lookup fields into another list, they're carried
    verbatim as `<FieldName>Id` (the numeric id). Same caveat.
  * The list's entity-type name is discovered dynamically so this
    script survives list rename / recreate.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import quote

import requests
from requests_ntlm import HttpNtlmAuth


SYSTEM_FIELDS = frozenset({
    "__metadata", "Id", "ID", "GUID", "Title",
    "Author", "Editor", "AuthorId", "EditorId",
    "Created", "Modified",
    "ContentType", "ContentTypeId", "FileSystemObjectType",
    "Attachments", "AttachmentFiles",
    "OData__UIVersionString", "OData__ColorTag",
    "ComplianceAssetId",
    "ServerRedirectedEmbedUri", "ServerRedirectedEmbedUrl",
    "OwshiddenversionField",  # SP-2013-ish
})


def get_form_digest(sess: requests.Session, base: str) -> str:
    r = sess.post(
        f"{base}/_api/contextinfo",
        headers={"Accept": "application/json;odata=verbose"},
    )
    r.raise_for_status()
    return r.json()["d"]["GetContextWebInformation"]["FormDigestValue"]


def get_entity_type(sess: requests.Session, base: str, list_name: str) -> str:
    url = (
        f"{base}/_api/web/lists/getbytitle('{quote(list_name)}')"
        f"?$select=ListItemEntityTypeFullName"
    )
    r = sess.get(url, headers={"Accept": "application/json;odata=verbose"})
    r.raise_for_status()
    return r.json()["d"]["ListItemEntityTypeFullName"]


def query_items(
    sess: requests.Session, base: str, list_name: str, filter_expr: str,
) -> list[dict]:
    """Page through results; SP defaults to 100 per page. $top raises the cap."""
    url = (
        f"{base}/_api/web/lists/getbytitle('{quote(list_name)}')/items"
        f"?$filter={filter_expr}&$top=5000"
    )
    results: list[dict] = []
    while url:
        r = sess.get(url, headers={"Accept": "application/json;odata=verbose"})
        r.raise_for_status()
        payload = r.json()["d"]
        results.extend(payload.get("results", []))
        url = payload.get("__next")
    return results


def create_item(
    sess: requests.Session, base: str, list_name: str, entity_type: str,
    digest: str, fields: dict,
) -> dict:
    url = f"{base}/_api/web/lists/getbytitle('{quote(list_name)}')/items"
    body = {"__metadata": {"type": entity_type}, **fields}
    r = sess.post(
        url, json=body,
        headers={
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json;odata=verbose",
            "X-RequestDigest": digest,
        },
    )
    if not r.ok:
        # Include SP's error body -- often names the problem field.
        raise RuntimeError(
            f"POST failed ({r.status_code}) creating "
            f"item_no={fields.get('item_no')!r}: {r.text[:400]}"
        )
    return r.json()["d"]


def _is_deferred(v) -> bool:
    """SP returns nav-property stubs as {'__deferred': {'uri': ...}}.
    These are expandable references (Author, Editor, FieldValuesAsText,
    AttachmentFiles, ParentList, File, etc.), not scalar data, and SP
    rejects them on POST with:
      "The property '__deferred' does not exist on type 'SP.SecurableObject'."
    """
    return isinstance(v, dict) and "__deferred" in v


def strip_system_fields(row: dict) -> dict:
    return {
        k: v for k, v in row.items()
        if k not in SYSTEM_FIELDS and not _is_deferred(v)
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="log what would be created, don't write")
    args = ap.parse_args()

    site = os.environ.get("HILDA_SP_SITE")
    user = os.environ.get("HILDA_SP_USER")
    pw = os.environ.get("HILDA_SP_PASS")
    if not (site and user and pw):
        print("ERROR: set HILDA_SP_SITE / HILDA_SP_USER / HILDA_SP_PASS env vars",
              file=sys.stderr)
        return 2

    list_name = os.environ.get("TEMPLATE_LIST_NAME", "Deliverables_Template")
    milestones = [
        m.strip() for m in os.environ.get("MILESTONES", "DRR,P1").split(",")
        if m.strip()
    ]

    base = site.rstrip("/")
    sess = requests.Session()
    sess.auth = HttpNtlmAuth(user, pw)

    print(f"* SP site      = {base}")
    print(f"* List         = {list_name}")
    print(f"* Milestones   = {milestones}")
    print(f"* Dry-run      = {args.dry_run}")
    print()

    entity_type = get_entity_type(sess, base, list_name)
    print(f"* Entity type  = {entity_type}")
    print()

    # Milestone list -> OData 'or' filter string, quoted values.
    milestone_clause = " or ".join(
        f"milestone_name eq '{m}'" for m in milestones
    )
    vzw_filter = f"(carrier eq 'VZW') and ({milestone_clause})"
    mmk_filter = f"(carrier eq 'MMK') and ({milestone_clause})"

    print(f"Fetching VZW template rows...")
    vzw_rows = query_items(sess, base, list_name, vzw_filter)
    print(f"  found {len(vzw_rows)} VZW rows")

    print(f"Fetching existing MMK template rows (idempotency check)...")
    mmk_rows = query_items(sess, base, list_name, mmk_filter)
    existing = {
        (r.get("milestone_name"), r.get("item_no")) for r in mmk_rows
    }
    print(f"  found {len(mmk_rows)} existing MMK rows"
          f" (will skip these keys on clone)")
    print()

    digest = None if args.dry_run else get_form_digest(sess, base)

    created = 0
    skipped = 0
    failed = 0

    for src in vzw_rows:
        key = (src.get("milestone_name"), src.get("item_no"))
        if key in existing:
            skipped += 1
            print(f"  SKIP  milestone={key[0]:<4} item_no={key[1]:<3} "
                  f"(MMK row already present)")
            continue

        payload = strip_system_fields(src)
        payload["carrier"] = "MMK"

        if args.dry_run:
            print(f"  WOULD CREATE  milestone={key[0]:<4} item_no={key[1]:<3} "
                  f"tg_name={payload.get('tg_name', '')!r}")
            created += 1
            continue

        try:
            new_row = create_item(sess, base, list_name, entity_type,
                                  digest, payload)
            new_id = new_row.get("Id") or new_row.get("ID")
            print(f"  CREATED  milestone={key[0]:<4} item_no={key[1]:<3} "
                  f"new Id={new_id}")
            created += 1
        except Exception as exc:
            failed += 1
            print(f"  FAIL     milestone={key[0]:<4} item_no={key[1]:<3} "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    print()
    print(f"Summary: created={created}, skipped_existing={skipped}, "
          f"failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
