"""Milestone 8 — status probe: per-instrument presence, green/amber/red.

Each test builds a synthetic ``/sys`` tree (a real directory of files, no mocks)
and a fake ``/proc/mounts``, then asserts the probe's verdict. The probe must
never read the live machine, so every sysfs root is injected explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server import status as S


def make_block(root: Path, name: str, vendor: str = "", model: str = "", removable: bool = False) -> Path:
    device_dir = root / name
    device_dir.mkdir(parents=True)
    (device_dir / "removable").write_text("1" if removable else "0")
    device = device_dir / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor)
    (device / "model").write_text(model)
    return device_dir


def make_mounts(root: Path, source: str, mount: Path, fstype: str = "vfat") -> Path:
    mounts = root / "mounts"
    mounts.write_text(f"{source} {mount} {fstype} rw 0 0\n")
    return mounts


def make_tty_acm(tty: Path, usb: Path, name: str, vendor: str, product: str) -> Path:
    """A ttyACM entry whose USB ancestry carries vendor/product ids.

    The ``device`` symlink points at the USB interface; its parent (``..``) is
    the USB device that owns idVendor/idProduct, matching real sysfs.
    """
    tty_entry = tty / name
    tty_entry.mkdir(parents=True)
    device_dir = usb / f"{name}-device"
    interface_dir = device_dir / f"{name}:1.0"
    interface_dir.mkdir(parents=True)
    (device_dir / "idVendor").write_text(vendor)
    (device_dir / "idProduct").write_text(product)
    os.symlink(interface_dir, tty_entry / "device")
    return tty_entry


def empty_world(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    block = tmp_path / "block"
    tty = tmp_path / "tty"
    usb = tmp_path / "usb"
    mounts = tmp_path / "mounts"
    staging = tmp_path / "staging"
    for d in (block, tty, usb, staging):
        d.mkdir()
    mounts.write_text("")
    return block, tty, usb, mounts, staging


def probe(world, mounter=None, now=None) -> dict[str, S.DeviceStatus]:
    block, tty, usb, mounts, staging = world
    devices = S.probe(
        block_dir=block,
        tty_dir=tty,
        usb_dir=usb,
        mounts_path=mounts,
        staging_dir=staging,
        mounter=mounter,
        now=now,
    )
    return {d.id: d for d in devices}


def recording_mounter(mount_point: str | None = None):
    """A mounter that records every device it is asked to mount and returns
    ``mount_point`` (None simulates a failed mount)."""
    calls: list[str] = []

    def _mount(device_path: str) -> str | None:
        calls.append(device_path)
        return mount_point

    return _mount, calls


@pytest.fixture(autouse=True)
def _reset_mount_throttle():
    S._last_mount_attempt.clear()
    yield
    S._last_mount_attempt.clear()


def test_probe_returns_all_five_in_spec_order(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    devices = S.probe(
        block_dir=block, tty_dir=tty, usb_dir=usb, mounts_path=mounts, staging_dir=staging
    )
    assert [d.id for d in devices] == ["op1", "spotykach", "wave_bard", "sp404", "fx_wizard"]


def test_every_device_red_when_nothing_present(tmp_path):
    states = {i: d.state for i, d in probe(empty_world(tmp_path)).items()}
    assert states == {
        "op1": "red",
        "spotykach": "red",
        "wave_bard": "red",
        "sp404": "red",
        "fx_wizard": "red",
    }


def test_op1_green_when_mounted(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    mount_point = tmp_path / "54FF-1FEE"
    mount_point.mkdir()
    make_mounts(tmp_path, "/dev/sda", mount_point)
    result = probe((block, tty, usb, mounts, staging))
    assert result["op1"].state == "green"
    assert result["op1"].present is True
    assert "54FF-1FEE" in result["op1"].detail


def test_op1_amber_when_detected_but_unmounted(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    result = probe((block, tty, usb, mounts, staging))
    assert result["op1"].state == "amber"
    assert result["op1"].present is True
    assert "not mounted" in result["op1"].detail


def test_spotykach_green_when_tty_acm_present(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_tty_acm(tty, usb, "ttyACM0", vendor="0483", product="5740")
    result = probe((block, tty, usb, mounts, staging))
    assert result["spotykach"].state == "green"
    assert result["spotykach"].present is True
    assert "ttyACM0" in result["spotykach"].detail


def test_spotykach_red_when_tty_acm_is_not_spotykach(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_tty_acm(tty, usb, "ttyACM0", vendor="0582", product="02e7")  # SP-404MK2, also ttyACM
    result = probe((block, tty, usb, mounts, staging))
    assert result["spotykach"].state == "red"
    assert result["spotykach"].present is False


def test_spotykach_red_when_bare_tty_acm_has_no_usb_ids(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    (tty / "ttyACM0").mkdir()  # a ttyACM with no USB ancestry is not a Spotykach
    result = probe((block, tty, usb, mounts, staging))
    assert result["spotykach"].state == "red"


def test_sp404_green_when_import_card_mounted(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "mmcblk0")
    card = tmp_path / "card"
    (card / "IMPORT").mkdir(parents=True)
    make_mounts(tmp_path, "/dev/mmcblk0p1", card, fstype="exfat")
    result = probe((block, tty, usb, mounts, staging))
    assert result["sp404"].state == "green"
    assert result["sp404"].present is True
    assert "IMPORT" in result["sp404"].detail


def test_sp404_red_when_sd_card_has_no_import_folder(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "mmcblk0")
    media_card = tmp_path / "media"
    media_card.mkdir()  # a media SD card: mounted, but no IMPORT/ folder
    make_mounts(tmp_path, "/dev/mmcblk0p1", media_card, fstype="exfat")
    result = probe((block, tty, usb, mounts, staging))
    assert result["sp404"].state == "red"
    assert result["sp404"].present is False


def test_sp404_ignores_op1_as_a_card(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    op1_mount = tmp_path / "54FF-1FEE"
    op1_mount.mkdir()
    (op1_mount / "IMPORT").mkdir()  # OP-1 has no IMPORT, but be strict: never mislabel OP-1
    make_mounts(tmp_path, "/dev/sda", op1_mount)
    result = probe((block, tty, usb, mounts, staging))
    assert result["op1"].state == "green"
    assert result["sp404"].state == "red"


def test_sp404_amber_when_sd_reader_behind_usb_hub(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    root_hub = usb / "1-1"
    root_hub.mkdir(parents=True)
    (root_hub / "idVendor").write_text("1d6b")
    (root_hub / "bDeviceClass").write_text("09")
    downstream_hub = usb / "1-1" / "1-1.1"
    downstream_hub.mkdir(parents=True)
    (downstream_hub / "idVendor").write_text("05e3")
    (downstream_hub / "bDeviceClass").write_text("09")
    reader = downstream_hub / "1-1.1.1"
    reader.mkdir(parents=True)
    (reader / "idVendor").write_text("0781")
    (reader / "bDeviceClass").write_text("00")

    sdb = block / "sdb"
    sdb.mkdir(parents=True)
    (sdb / "removable").write_text("1")
    os.symlink(reader, sdb / "device")

    card = tmp_path / "reader-card"
    (card / "IMPORT").mkdir(parents=True)
    make_mounts(tmp_path, "/dev/sdb1", card)
    result = probe((block, tty, usb, mounts, staging))
    assert result["sp404"].state == "amber"
    assert "replug" in result["sp404"].detail


def test_wave_bard_amber_when_draft_staged(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    wave_bard = staging / "wave_bard"
    wave_bard.mkdir(parents=True)
    (wave_bard / "manifest.json").write_text(json.dumps({"state": "ready_to_flash"}))
    result = probe((block, tty, usb, mounts, staging))
    assert result["wave_bard"].state == "amber"
    assert result["wave_bard"].present is True
    assert "ready to flash" in result["wave_bard"].detail


def test_fx_wizard_red_when_no_draft(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    result = probe((block, tty, usb, mounts, staging))
    assert result["fx_wizard"].state == "red"
    assert result["fx_wizard"].present is False


def test_fx_wizard_amber_when_draft_staged(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    fx_wizard = staging / "fx_wizard"
    fx_wizard.mkdir(parents=True)
    (fx_wizard / "manifest.json").write_text(json.dumps({"state": "ready_to_flash"}))
    result = probe((block, tty, usb, mounts, staging))
    assert result["fx_wizard"].state == "amber"
    assert result["fx_wizard"].present is True


def test_status_payload_serializes_devices(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    payload = S.status_payload(
        block_dir=block, tty_dir=tty, usb_dir=usb, mounts_path=mounts, staging_dir=staging
    )
    assert set(payload) == {"devices", "generated_at"}
    assert len(payload["devices"]) == 5
    assert payload["devices"][0]["id"] == "op1"
    assert set(payload["devices"][0]) == {"id", "label", "transport", "state", "present", "detail"}


def test_op1_auto_mounts_when_unmounted(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    mounter, calls = recording_mounter(mount_point="/run/media/rjodouin/54FF-1FEE")
    result = probe((block, tty, usb, mounts, staging), mounter=mounter)
    assert result["op1"].state == "green"
    assert calls == ["/dev/sda"]
    assert "54FF-1FEE" in result["op1"].detail


def test_op1_amber_when_auto_mount_fails(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    mounter, calls = recording_mounter(mount_point=None)  # mount fails
    result = probe((block, tty, usb, mounts, staging), mounter=mounter)
    assert result["op1"].state == "amber"
    assert calls == ["/dev/sda"]
    assert "udisksctl" in result["op1"].detail


def test_sp404_auto_mounts_unmounted_card_with_import(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "mmcblk0")
    partition = block / "mmcblk0" / "mmcblk0p1"
    partition.mkdir()
    (partition / "partition").write_text("1")
    card = tmp_path / "card"
    (card / "IMPORT").mkdir(parents=True)
    mounter, calls = recording_mounter(mount_point=str(card))
    result = probe((block, tty, usb, mounts, staging), mounter=mounter)
    assert result["sp404"].state == "green"
    assert calls == ["/dev/mmcblk0p1"]


def test_sp404_red_when_auto_mounted_card_has_no_import(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "mmcblk0")
    partition = block / "mmcblk0" / "mmcblk0p1"
    partition.mkdir()
    (partition / "partition").write_text("1")
    media_card = tmp_path / "media"
    media_card.mkdir()  # mounted by the mounter, but no IMPORT/ folder
    mounter, calls = recording_mounter(mount_point=str(media_card))
    result = probe((block, tty, usb, mounts, staging), mounter=mounter)
    assert result["sp404"].state == "red"
    assert calls == ["/dev/mmcblk0p1"]


def test_sp404_red_when_unmounted_and_no_mounter(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "mmcblk0")
    result = probe((block, tty, usb, mounts, staging))  # read-only: no mounter
    assert result["sp404"].state == "red"


def test_no_mount_when_already_mounted(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    mount_point = tmp_path / "54FF-1FEE"
    mount_point.mkdir()
    make_mounts(tmp_path, "/dev/sda", mount_point)

    def explode(device_path):
        raise AssertionError(f"must not mount an already-mounted device: {device_path}")

    result = probe((block, tty, usb, mounts, staging), mounter=explode)
    assert result["op1"].state == "green"


def test_mount_throttled_within_retry_window(tmp_path):
    block, tty, usb, mounts, staging = empty_world(tmp_path)
    make_block(block, "sda", vendor="Teenage", model="OP-1", removable=True)
    mounter, calls = recording_mounter(mount_point=None)  # always fails
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    probe((block, tty, usb, mounts, staging), mounter=mounter, now=now)
    probe((block, tty, usb, mounts, staging), mounter=mounter, now=now)
    assert len(calls) == 1  # second attempt suppressed by the throttle

    clock["t"] = S.MOUNT_RETRY_SECONDS + 1.0
    probe((block, tty, usb, mounts, staging), mounter=mounter, now=now)
    assert len(calls) == 2
