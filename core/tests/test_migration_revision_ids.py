"""Guards the alembic revision chain against defects that only appear on a
real database.

`0005` originally used its own column name as the revision id
(`0005_upload_target_folder_override`, 34 chars). Alembic creates
`alembic_version.version_num` as VARCHAR(32), so `upgrade head` failed on the
corp box with StringDataRightTruncationError -- and because the DDL and the
version bookkeeping share one transaction, the column never landed. Nothing
caught it earlier: tests build schema from `Base.metadata.create_all()`, so no
test in the suite runs a migration.

`0003` is already 29 characters, so the margin is thin enough to be worth a
test rather than a comment.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

VERSIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "storage" / "migrations" / "versions"
)

# Alembic's default `version_table` column width. Widening it is not a fix:
# a fresh database recreates the table at this width.
MAX_VERSION_NUM_LEN = 32


def _assignments(path: pathlib.Path) -> dict[str, object]:
    """Module-level literal assignments, read without importing -- importing a
    migration executes `from alembic import op` against no context."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return out


def _migrations() -> list[tuple[pathlib.Path, dict[str, object]]]:
    return [(p, _assignments(p)) for p in sorted(VERSIONS_DIR.glob("[0-9]*.py"))]


def test_versions_dir_is_found():
    """A wrong path here would make every assertion below vacuous."""
    assert VERSIONS_DIR.is_dir(), VERSIONS_DIR
    assert len(_migrations()) >= 5


@pytest.mark.parametrize(
    "path,data", _migrations(), ids=lambda v: v.name if hasattr(v, "name") else "",
)
def test_revision_id_fits_alembic_version_num(path, data):
    revision = data.get("revision")
    assert isinstance(revision, str) and revision, f"{path.name}: no revision id"
    assert len(revision) <= MAX_VERSION_NUM_LEN, (
        f"{path.name}: revision id {revision!r} is {len(revision)} chars; "
        f"alembic_version.version_num holds {MAX_VERSION_NUM_LEN}. "
        "`upgrade head` fails and rolls back the whole migration."
    )


@pytest.mark.parametrize(
    "path,data", _migrations(), ids=lambda v: v.name if hasattr(v, "name") else "",
)
def test_down_revision_also_fits(path, data):
    """A too-long down_revision would not truncate on write, but it could never
    match the stored value, so the migration would be unreachable."""
    down = data.get("down_revision")
    if down is None:
        return
    assert isinstance(down, str)
    assert len(down) <= MAX_VERSION_NUM_LEN, f"{path.name}: {down!r}"


def test_chain_is_linear_and_resolves():
    """Every down_revision names a real revision, exactly one base, exactly one
    head. A dangling down_revision fails at runtime, not at import."""
    by_id = {data["revision"]: data for _, data in _migrations()}
    assert len(by_id) == len(_migrations()), "duplicate revision id"

    bases = [r for r, d in by_id.items() if d.get("down_revision") is None]
    assert len(bases) == 1, f"expected one base, got {bases}"

    for rev, data in by_id.items():
        down = data.get("down_revision")
        if down is not None:
            assert down in by_id, f"{rev} revises unknown {down!r}"

    claimed = {d["down_revision"] for d in by_id.values() if d.get("down_revision")}
    heads = set(by_id) - claimed
    assert len(heads) == 1, f"expected one head, got {sorted(heads)}"


def test_filename_matches_revision_id():
    """Not required by alembic, but a mismatch makes the chain hard to follow
    and hid the length bug behind a plausible-looking filename."""
    for path, data in _migrations():
        assert path.stem == data["revision"], (
            f"{path.name} declares revision {data['revision']!r}"
        )
