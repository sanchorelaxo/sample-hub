"""RP2040 UF2 flash staging — device-free tests for the dry-run and write path.

``stage_uf2`` writes a ``.uf2`` image onto an RP2040 boot volume, which flashes
and resets the unit, so the actual write runs only when ``dry_run=False``. These
tests exercise the full path against a fake boot volume under ``tmp_path``, so
no real hardware is touched (a write there is harmless).
"""

from __future__ import annotations

import pytest

from server.devices.flashable import FlashError, stage_uf2
from server.uf2 import BLOCK_SIZE, UF2_FAMILY_ID, generate_uf2


def _fake_boot_volume(tmp_path):
    vol = tmp_path / "RPI-RP2"
    vol.mkdir()
    (vol / "INFO_UF2.TXT").write_text(
        "UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n"
    )
    return vol


def _image(size: int = 256) -> bytes:
    return generate_uf2(UF2_FAMILY_ID, 0x10080000, bytes(range(size)))


def test_dry_run_returns_plan_without_writing(tmp_path):
    vol = _fake_boot_volume(tmp_path)
    data = _image()
    result = stage_uf2(vol, data, dry_run=True)
    assert result["dry_run"] is True
    assert result["filename"] == "firmware.uf2"
    assert result["bytes"] == len(data)
    assert result["blocks"] == len(data) // BLOCK_SIZE
    # nothing written: only the boot-info file remains
    assert sorted(p.name for p in vol.iterdir()) == ["INFO_UF2.TXT"]


def test_flash_writes_uf2(tmp_path):
    vol = _fake_boot_volume(tmp_path)
    data = _image()
    result = stage_uf2(vol, data, dry_run=False)
    assert result["dry_run"] is False
    assert (vol / "firmware.uf2").read_bytes() == data
    assert not (vol / "firmware.uf2.part").exists()


def test_stage_rejects_invalid_uf2(tmp_path):
    vol = _fake_boot_volume(tmp_path)
    with pytest.raises(FlashError):
        stage_uf2(vol, b"not a uf2", dry_run=True)


def test_stage_rejects_non_boot_volume(tmp_path):
    vol = tmp_path / "not-a-boot-volume"
    vol.mkdir()
    with pytest.raises(FlashError):
        stage_uf2(vol, _image(), dry_run=True)


def test_stage_rejects_bad_filename(tmp_path):
    vol = _fake_boot_volume(tmp_path)
    with pytest.raises(FlashError):
        stage_uf2(vol, _image(), filename="evil.bin", dry_run=True)


def test_stage_rejects_path_traversal_filename(tmp_path):
    vol = _fake_boot_volume(tmp_path)
    with pytest.raises(FlashError):
        stage_uf2(vol, _image(), filename="../evil.uf2", dry_run=True)
