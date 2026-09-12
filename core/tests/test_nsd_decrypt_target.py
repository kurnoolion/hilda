"""NASCA-CARRIER-1 (2026-09-11) tests -- carrier subdir resolution for
the pre-walk DRM decrypt call.

Contract:
  * carrier-allowlist customer (e.g. MMK: ("VZW", "Verizon"))
    -> first subdir in tuple order that exists on disk (VZW preferred).
  * carrier-allowlist customer, no subdir exists -> None.
  * non-allowlist customer -> device_folder unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.src.workflow_engine.tasks.nsd2_poll import (
    _carrier_allowlist_repr,
    _resolve_decrypt_target,
)


@pytest.fixture
def device_folder(tmp_path: Path) -> Path:
    root = tmp_path / "S671U1"
    root.mkdir()
    return root


class TestAllowlistCustomer:
    def test_vzw_preferred_over_verizon_when_both_exist(self, device_folder):
        (device_folder / "VZW").mkdir()
        (device_folder / "Verizon").mkdir()
        target = _resolve_decrypt_target(device_folder, "MMK")
        assert target == device_folder / "VZW"

    def test_verizon_used_when_only_verizon_exists(self, device_folder):
        (device_folder / "Verizon").mkdir()
        target = _resolve_decrypt_target(device_folder, "MMK")
        assert target == device_folder / "Verizon"

    def test_vzw_used_when_only_vzw_exists(self, device_folder):
        (device_folder / "VZW").mkdir()
        target = _resolve_decrypt_target(device_folder, "MMK")
        assert target == device_folder / "VZW"

    def test_none_when_neither_subdir_exists(self, device_folder):
        # An unrelated subdir doesn't count.
        (device_folder / "Audio(Done)").mkdir()
        target = _resolve_decrypt_target(device_folder, "MMK")
        assert target is None

    def test_file_not_dir_treated_as_missing(self, device_folder):
        # If "VZW" is a file (bogus but possible on a broken mount), fall
        # through to Verizon -- is_dir must be True.
        (device_folder / "VZW").write_text("not a dir")
        (device_folder / "Verizon").mkdir()
        target = _resolve_decrypt_target(device_folder, "MMK")
        assert target == device_folder / "Verizon"


class TestNonAllowlistCustomer:
    def test_device_folder_returned_unchanged(self, device_folder):
        # customer_id not in CARRIER_ALLOWED_ROOT_FOLDERS -> return input.
        target = _resolve_decrypt_target(device_folder, "SOME_OTHER")
        assert target == device_folder

    def test_device_folder_returned_unchanged_even_if_vzw_exists(self, device_folder):
        # Non-allowlist customer never descends into carrier subdir even
        # when one happens to be present -- allowlist is the sole gate.
        (device_folder / "VZW").mkdir()
        target = _resolve_decrypt_target(device_folder, "SOME_OTHER")
        assert target == device_folder


class TestAllowlistRepr:
    def test_mmk_repr(self):
        assert _carrier_allowlist_repr("MMK") == "VZW,Verizon"

    def test_non_allowlist_repr_is_empty(self):
        assert _carrier_allowlist_repr("OTHER") == ""
