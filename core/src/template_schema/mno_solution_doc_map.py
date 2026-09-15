"""MNO-Solution PLM doc-to-multi-item routing map (MNO-MULTIASSOC-1).

The on-prem `plm_file_download.py` script, when it fetches a PLM ticket for the
MNO-Solution TG, also writes a per-batch YAML at `<work_dir>/mno_solution_doc_map.yaml`
naming which downloaded filenames belong to which item_no(s) on that plm_id.
A single filename may legitimately appear under multiple item_no entries (one
file body → N `document_item_association` rows on ingest).

This module loads that YAML and exposes a reverse index
`normalized_basename -> [delivery_item_id, ...]` so `plm_poll._ingest_new_plm_file`
can pass `pre_routed_item_ids` into `Fr52AttachmentRouter.route(...)` and skip
FR-52's template.yaml pattern-match step for those files. Dedup, doc_type
classification, revision numbering, persist, and view-tree writes all still
run through Fr52 unchanged.

YAML shape (produced by the on-prem script):

    plm_id: "CQ12345"
    mappings:
      - item_no: 40
        documents: ["Panel Spec.pdf", "SAR Report.docx"]
      - item_no: 41
        documents: ["Panel Spec.pdf"]
      - item_no: 42
        documents: []

TG is implicit (always MNO-Solution). device_id and milestone_id are not
carried in the YAML — the caller scopes them by passing only that batch's
items to `load_mno_solution_doc_map`.

Failure policy — always fall through to FR-52 template.yaml routing:
  - YAML file missing            -> None, silent (most PLM downloads have no yaml)
  - YAML unreadable / malformed  -> None, WARN
  - plm_id mismatch              -> None, WARN
  - item_no not in provided items -> skip that entry, WARN, continue on remaining entries
  - documents: []                -> valid (item contributes nothing to the index)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

_YAML_NAME = "mno_solution_doc_map.yaml"


def _normalize_filename(name: str) -> str:
    """basename(name).strip().lower() per CLASSIFY-BASENAME-1.

    Extensions are kept ("Panel Spec.pdf" and "Panel Spec.docx" are distinct).
    Path separators (both `/` and `\\`) are stripped so callers may pass full
    paths without care."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base.strip().lower()


@dataclass(frozen=True)
class MnoSolutionDocMap:
    """Loaded MNO-Solution doc map for one PLM batch."""

    plm_id: str
    by_filename: dict[str, list[str]] = field(default_factory=dict)

    def item_ids_for(self, filename: str) -> list[str] | None:
        """Return the delivery_item_ids this filename maps to, or None if
        the filename is absent from the map.

        None means "not in yaml -- fall through to FR-52 template.yaml routing".
        An empty list is never returned (an item with `documents: []`
        contributes nothing to the reverse index)."""
        ids = self.by_filename.get(_normalize_filename(filename))
        return list(ids) if ids else None


def load_mno_solution_doc_map(
    work_dir: Path,
    items: list[Any],
    *,
    expected_plm_id: str,
) -> MnoSolutionDocMap | None:
    """Load `<work_dir>/mno_solution_doc_map.yaml` and build the reverse index.

    `items` is the caller's per-TG item list (the same list passed into
    `_ingest_new_plm_file`); each element must expose `item_no` and a
    delivery_item_id (via `item_id` or `delivery_item_id`).

    Returns None on any of: file missing, unreadable, malformed, or plm_id
    mismatch. The caller then falls through to normal FR-52 routing."""
    yaml_path = work_dir / _YAML_NAME
    if not yaml_path.exists():
        return None

    try:
        raw_text = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning(
            "MNO_DOC_MAP: read failed path=%s: %s",
            yaml_path, exc,
        )
        return None

    try:
        parsed = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        _log.warning(
            "MNO_DOC_MAP: yaml parse failed path=%s: %s",
            yaml_path, exc,
        )
        return None

    if not isinstance(parsed, dict):
        _log.warning(
            "MNO_DOC_MAP: top level is %s (expected mapping) path=%s",
            type(parsed).__name__, yaml_path,
        )
        return None

    plm_id_in_yaml = str(parsed.get("plm_id") or "").strip()
    if plm_id_in_yaml != expected_plm_id:
        _log.warning(
            "MNO_DOC_MAP: plm_id mismatch yaml=%r expected=%r path=%s -- ignoring",
            plm_id_in_yaml, expected_plm_id, yaml_path,
        )
        return None

    raw_mappings = parsed.get("mappings") or []
    if not isinstance(raw_mappings, list):
        _log.warning(
            "MNO_DOC_MAP: mappings is %s (expected list) path=%s",
            type(raw_mappings).__name__, yaml_path,
        )
        return None

    item_no_to_id: dict[int, str] = {}
    for it in items:
        no = _coerce_item_no(getattr(it, "item_no", None))
        iid = getattr(it, "item_id", None) or getattr(it, "delivery_item_id", None)
        if no is not None and iid:
            item_no_to_id[no] = str(iid)

    by_filename: dict[str, list[str]] = {}
    for entry in raw_mappings:
        if not isinstance(entry, dict):
            _log.warning(
                "MNO_DOC_MAP: mapping entry not a dict (%s) plm_id=%s -- skipping",
                type(entry).__name__, expected_plm_id,
            )
            continue
        item_no = _coerce_item_no(entry.get("item_no"))
        if item_no is None:
            _log.warning(
                "MNO_DOC_MAP: mapping entry missing/invalid item_no plm_id=%s entry=%r -- skipping",
                expected_plm_id, entry,
            )
            continue
        delivery_item_id = item_no_to_id.get(item_no)
        if delivery_item_id is None:
            _log.warning(
                "MNO_DOC_MAP: item_no=%d in yaml but no matching Postgres item "
                "for plm_id=%s -- skipping entry",
                item_no, expected_plm_id,
            )
            continue
        documents = entry.get("documents") or []
        if not isinstance(documents, list):
            _log.warning(
                "MNO_DOC_MAP: item_no=%d documents is %s (expected list) plm_id=%s -- skipping",
                item_no, type(documents).__name__, expected_plm_id,
            )
            continue
        for doc_name in documents:
            if not isinstance(doc_name, str) or not doc_name.strip():
                continue
            key = _normalize_filename(doc_name)
            bucket = by_filename.setdefault(key, [])
            if delivery_item_id not in bucket:
                bucket.append(delivery_item_id)

    _log.info(
        "MNO_DOC_MAP: loaded plm_id=%s files=%d total_associations=%d path=%s",
        expected_plm_id, len(by_filename),
        sum(len(v) for v in by_filename.values()), yaml_path,
    )
    return MnoSolutionDocMap(plm_id=expected_plm_id, by_filename=by_filename)


def _coerce_item_no(raw: Any) -> int | None:
    """Accept int or numeric-string item_no; return None for anything else."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None
