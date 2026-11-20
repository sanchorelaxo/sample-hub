"""Milestone 8 — the status probe: one verdict per instrument, green/amber/red.

SPEC.md §5.1 wants a presence dot on every pane before a drop is allowed:
green = the instrument is present and its transport is usable now; amber =
present but blocked on a user step (needs a mount, a flash, a direct port);
red = absent. The probe reads only sysfs and /proc/mounts, so it never needs
the device writers imported.

By default a probe is read-only: it reports what is mounted but does not
mount anything. Pass ``mounter`` to auto-mount a visible-but-unmounted OP-1
or SD card on the same pass, so a card that is inserted, ejected, and
re-inserted becomes usable on the next poll instead of waiting for a human to
run udisksctl by hand.

Every sysfs root is an explicit argument so the real-device tests and this
module share one entry point and unit tests can point it at a synthetic tree
instead of the machine.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from server.devices import READY_TO_FLASH
from server.device_tree import APP, DEVICE_ORDER, LABELS

GREEN = "green"
AMBER = "amber"
RED = "red"

# Pane order + labels come from the device tree (server/devices.json) — the
# single source of truth for the rack per TODO_1.txt item 6.

# idVendor of the Linux root hub; downstream hubs share bDeviceClass 09 but a
# different vendor, so the "on a hub" check ignores this one.
ROOT_HUB_VENDOR = "1d6b"
HUB_CLASS = "09"

# The Spotykach exposes VID 0483 / PID 5740 in BOTH of its USB modes: USB MIDI
# (its default) and USB CDC ACM transfer mode. The SP-404MK2 is also a CDC ACM
# port, so presence is the Spotykach's VID/PID rather than a bare ttyACM match.
SPOTYKACH_VENDOR = 0x0483
SPOTYKACH_PRODUCT = 0x5740

# How long to wait between mount attempts on the same device. The status
# endpoint polls often, so without a throttle a card that cannot mount would
# hammer udisksctl on every poll.
MOUNT_RETRY_SECONDS = APP.get("mount_retry_seconds", 10.0)

# A mounter takes a device node like "/dev/sda" or "/dev/mmcblk0p1" and returns
# the mount point it landed at, or None when the mount was refused or failed.
Mounter = Callable[[str], str | None]

_mounter_lock = threading.Lock()
_last_mount_attempt: dict[str, float] = {}


@dataclass(frozen=True)
class DeviceStatus:
    id: str
    label: str
    transport: str
    state: str
    present: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_staging_dir() -> Path:
    """The hub's staging root, where Wave Bard / FX Wizard drafts wait to flash."""
    base = Path(os.environ.get("SAMPLE_HUB_DATA", "~/.sample-hub")).expanduser()
    return base / "staging"


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _block_devices(block_dir: Path) -> list[dict]:
    """Enumerate physical block devices, skipping loop/dm/nvme noise."""
    devices: list[dict] = []
    if not block_dir.is_dir():
        return devices
    for entry in sorted(block_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(("loop", "dm-", "zram", "nvme", "ram")):
            continue
        device = entry / "device"
        devices.append(
            {
                "name": name,
                "vendor": _read_text(device / "vendor"),
                "model": _read_text(device / "model"),
                "removable": _read_text(entry / "removable") == "1",
            }
        )
    return devices


def _mount_for(mounts_text: str, device_name: str) -> str | None:
    """Mount point for a block device, or None. Matches /dev/sda and /dev/sda1."""
    source = f"/dev/{device_name}"
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        device_path = parts[0]
        if device_path == source:
            return parts[1]
        remainder = device_path[len(source):] if device_path.startswith(source) else ""
        if remainder.isdigit() or (remainder.startswith("p") and remainder[1:].isdigit()):
            return parts[1]
    return None


def _is_op1(block: dict) -> bool:
    return "teenage" in block["vendor"].lower() and "op-1" in block["model"].lower()


def _is_rp2_boot(block: dict) -> bool:
    """An RP2040 in its UF2 bootloader reports vendor RPI and model RP2.

    A Wave Bard and an FX Wizard are the same RP2040 board, and in update mode
    both present this identical identity, so this is a platform check, not a
    per-instrument one.
    """
    return "rpi" in block["vendor"].lower() and "rp2" in block["model"].lower()


def _mount_targets(block_dir: Path, device_name: str) -> list[str]:
    """Device node(s) to mount for a block device: its partitions, or itself.

    The OP-1 formats the whole device (no partition table), so /dev/sda is the
    volume. An SD card is /dev/mmcblk0 with a /dev/mmcblk0p1 partition. Return
    the partition nodes when they exist, otherwise the raw device.
    """
    device_dir = block_dir / device_name
    partitions: list[str] = []
    if device_dir.is_dir():
        for entry in sorted(device_dir.iterdir()):
            if entry.is_dir() and (entry / "partition").is_file():
                partitions.append(entry.name)
    if partitions:
        return [f"/dev/{name}" for name in partitions]
    return [f"/dev/{device_name}"]


def _udisksctl_mount(device_path: str) -> str | None:
    """Mount a block device through udisks2 and return its mount point.

    udisksctl asks the session polkit agent for authorization, so it runs as
    the logged-in user with no sudo. Return None on failure (unmountable
    filesystem, no polkit agent, device vanished); callers turn that into an
    amber hint rather than raising.
    """
    try:
        result = subprocess.run(
            ["udisksctl", "mount", "-b", device_path],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"Mounted .* at (\S+)", result.stdout)
    if not match:
        return None
    return match.group(1).rstrip(".")


def _try_mount(device_path: str, mounter: Mounter, now: Callable[[], float]) -> str | None:
    """Mount a device at most once per retry window; None when throttled/failed."""
    with _mounter_lock:
        last = _last_mount_attempt.get(device_path)
        if last is not None and now() - last < MOUNT_RETRY_SECONDS:
            return None
        _last_mount_attempt[device_path] = now()
    return mounter(device_path)


def _probe_update_mode_devices(
    block_dir: Path, mounts_text: str, mounter: Mounter | None, now: Callable[[], float]
) -> list[dict]:
    """Kastle 2 (RP2040) devices currently in UF2 update mode, auto-mounted as needed.

    Update mode is the moment a board can receive a .uf2, so this is the "ready
    to flash" hardware signal for the Wave Bard and FX Wizard panes. Several
    boards may be attached at once; each is a plain RPI-RP2 volume with no
    per-unit identity, so callers treat the whole list as a shared pool rather
    than "the Wave Bard's board" vs "the FX Wizard's board".
    """
    update_mode_devices: list[dict] = []
    for block in _block_devices(block_dir):
        if not _is_rp2_boot(block):
            continue
        mount = _mount_for(mounts_text, block["name"])
        if not mount and mounter is not None:
            for target in _mount_targets(block_dir, block["name"]):
                mount = _try_mount(target, mounter, now)
                if mount:
                    break
        update_mode_devices.append({"name": block["name"], "mount": mount})
    return update_mode_devices


def _update_mode_phrase(update_mode_devices: list[dict]) -> str | None:
    """A human phrase for the boards currently in UF2 update mode, or None."""
    if not update_mode_devices:
        return None
    count = len(update_mode_devices)
    mounts = [d["mount"] for d in update_mode_devices if d["mount"]]
    if count == 1:
        where = f" at {mounts[0]}" if mounts else ""
        return f"Kastle 2 in update mode{where}"
    if mounts and len(mounts) == count:
        return f"{count} Kastle 2 boards in update mode ({', '.join(mounts)})"
    return f"{count} Kastle 2 boards in update mode"


def _probe_op1(
    block_dir: Path, mounts_text: str, mounter: Mounter | None, now: Callable[[], float]
) -> DeviceStatus:
    for block in _block_devices(block_dir):
        if not _is_op1(block):
            continue
        mount = _mount_for(mounts_text, block["name"])
        if mount:
            return DeviceStatus("op1", LABELS["op1"], "disk", GREEN, True, mount)
        if mounter is None:
            return DeviceStatus(
                "op1",
                LABELS["op1"],
                "disk",
                AMBER,
                True,
                f"detected /dev/{block['name']}, not mounted",
            )
        for target in _mount_targets(block_dir, block["name"]):
            mount = _try_mount(target, mounter, now)
            if mount:
                return DeviceStatus("op1", LABELS["op1"], "disk", GREEN, True, mount)
        return DeviceStatus(
            "op1",
            LABELS["op1"],
            "disk",
            AMBER,
            True,
            f"detected /dev/{block['name']}, auto-mount failed — "
            f"run: udisksctl mount -b /dev/{block['name']}",
        )
    return DeviceStatus("op1", LABELS["op1"], "disk", RED, False, "no OP-1 attached")


def _usb_ids(tty_sys_path: Path) -> tuple[int, int] | None:
    """(idVendor, idProduct) of the USB device behind a /sys tty entry.

    Mirrors spottytube-ext's ``sk_usb._usb_ids``: the tty's ``device`` symlink
    points at the USB interface, whose parent owns idVendor/idProduct.
    """
    for candidate in (tty_sys_path / "device" / "..", tty_sys_path / "device"):
        try:
            vendor = int((candidate / "idVendor").read_text().strip(), 16)
            product = int((candidate / "idProduct").read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        return vendor, product
    return None


def _spotykach_usb_present(usb_dir: Path) -> bool:
    """True when a USB device carries the Spotykach's VID/PID.

    The VID/PID is present in both USB modes (MIDI and CDC ACM transfer), so this
    is the presence signal; the mode is told apart by whether a ttyACM port also
    exists. Interface dirs under /sys/bus/usb/devices are symlinks with no idVendor
    of their own, so they fall through harmlessly.
    """
    vendor = f"{SPOTYKACH_VENDOR:04x}"
    product = f"{SPOTYKACH_PRODUCT:04x}"
    if not usb_dir.is_dir():
        return False
    for entry in usb_dir.iterdir():
        if not entry.is_dir():
            continue
        if _read_text(entry / "idVendor") == vendor and _read_text(entry / "idProduct") == product:
            return True
    return False


def _probe_spotykach(tty_dir: Path, usb_dir: Path) -> DeviceStatus:
    ports: list[str] = []
    if tty_dir.is_dir():
        for sys_path in sorted(tty_dir.glob("ttyACM*")):
            if _usb_ids(sys_path) == (SPOTYKACH_VENDOR, SPOTYKACH_PRODUCT):
                ports.append(f"/dev/{sys_path.name}")
    if ports:
        return DeviceStatus(
            "spotykach", LABELS["spotykach"], "usb_cdc", GREEN, True, ", ".join(ports)
        )
    if _spotykach_usb_present(usb_dir):
        # Present in its default USB MIDI mode. A write auto-switches it to
        # transfer mode (sk_usb.SkLink.open), so it is ready, not blocked.
        return DeviceStatus(
            "spotykach",
            LABELS["spotykach"],
            "usb_cdc",
            GREEN,
            True,
            "in MIDI mode — switches to transfer mode on write",
        )
    return DeviceStatus(
        "spotykach", LABELS["spotykach"], "usb_cdc", RED, False, "no Spotykach connected"
    )


def _on_usb_hub(block_dir: Path, device_name: str) -> bool:
    """True when a USB-backed block device hangs off a downstream hub (SPEC.md §8).

    Walks the block device's sysfs ``device`` symlink toward the controller and
    flags any ancestor that is itself a hub (bDeviceClass 09) but not the root
    hub (vendor 1d6b). Internal SD readers (mmcblk) have no USB ancestors, so
    they never trip this.
    """
    try:
        node = (block_dir / device_name / "device").resolve()
    except OSError:
        return False
    for ancestor in node.parents:
        vendor = _read_text(ancestor / "idVendor").lower()
        if not vendor:
            continue
        device_class = _read_text(ancestor / "bDeviceClass").strip().lower()
        if device_class in (HUB_CLASS, f"0x{HUB_CLASS}") and vendor != ROOT_HUB_VENDOR:
            return True
    return False


def _probe_sp404(
    block_dir: Path, mounts_text: str, mounter: Mounter | None, now: Callable[[], float]
) -> DeviceStatus:
    # The MK2's SD is read through a host card reader, not USB mass storage, so
    # presence is the card's SP-404 signature: a top-level IMPORT/ folder. That
    # keeps a plain media SD card (mounted, but no IMPORT/) from reading green.
    for block in _block_devices(block_dir):
        if _is_op1(block) or _is_rp2_boot(block):
            continue
        is_sd_card = block["name"].startswith("mmcblk") or block["removable"]
        if not is_sd_card:
            continue
        mount = _mount_for(mounts_text, block["name"])
        if not mount and mounter is not None:
            for target in _mount_targets(block_dir, block["name"]):
                mount = _try_mount(target, mounter, now)
                if mount:
                    break
        if not mount or not (Path(mount) / "IMPORT").is_dir():
            continue
        if _on_usb_hub(block_dir, block["name"]):
            return DeviceStatus(
                "sp404",
                LABELS["sp404"],
                "sd_card",
                AMBER,
                True,
                f"{mount} — replug direct, on USB hub",
            )
        return DeviceStatus(
            "sp404",
            LABELS["sp404"],
            "sd_card",
            GREEN,
            True,
            f"{mount} · manual IMPORT on unit",
        )
    return DeviceStatus(
        "sp404", LABELS["sp404"], "sd_card", RED, False, "no SP-404 SD card (IMPORT/ not found)"
    )


def _probe_staged(device_id: str, staging_dir: Path, update_mode_devices: list[dict]) -> DeviceStatus:
    """A staged-firmware instrument keys on both its draft and a board in update mode.

    Wave Bard and FX Wizard are the same RP2040 board; in update mode both mount
    as an identical RPI-RP2 volume, so a board in update mode is a shared signal,
    not a per-pane one. The pane goes green only when its draft is ready AND a
    board is actually in update mode to receive the flash.
    """
    manifest = staging_dir / device_id / "manifest.json"
    staged_state = None
    if manifest.is_file():
        try:
            staged_state = json.loads(manifest.read_text()).get("state")
        except (OSError, json.JSONDecodeError):
            staged_state = None

    board_count = len(update_mode_devices)
    phrase = _update_mode_phrase(update_mode_devices)

    if staged_state == READY_TO_FLASH and board_count:
        return DeviceStatus(
            device_id, LABELS[device_id], "staged_firmware", GREEN, True,
            f"ready to flash — {phrase}",
        )
    if staged_state == READY_TO_FLASH:
        return DeviceStatus(
            device_id, LABELS[device_id], "staged_firmware", AMBER, True,
            "draft staged — connect a Kastle 2 in update mode",
        )
    if staged_state is not None:
        return DeviceStatus(
            device_id, LABELS[device_id], "staged_firmware", AMBER, True, "draft staged",
        )
    if board_count:
        return DeviceStatus(
            device_id, LABELS[device_id], "staged_firmware", AMBER, True,
            f"{phrase} — no draft staged",
        )
    return DeviceStatus(
        device_id, LABELS[device_id], "staged_firmware", RED, False, "no draft staged"
    )


def probe(
    block_dir: Path | str = "/sys/block",
    tty_dir: Path | str = "/sys/class/tty",
    usb_dir: Path | str = "/sys/bus/usb/devices",
    mounts_path: Path | str = "/proc/mounts",
    staging_dir: Path | str | None = None,
    mounter: Mounter | None = None,
    now: Callable[[], float] | None = None,
) -> list[DeviceStatus]:
    """One pass over the live hardware (or an injected tree).

    With no ``mounter`` this is read-only detection. Pass ``mounter`` (and
    ``now`` for deterministic tests) to auto-mount visible-but-unmounted OP-1
    and SD-card volumes, so a freshly inserted device is usable on the next
    poll rather than waiting for a manual udisksctl.
    """
    block = Path(block_dir)
    tty = Path(tty_dir)
    usb = Path(usb_dir)
    mounts = Path(mounts_path)
    staging = Path(staging_dir) if staging_dir is not None else default_staging_dir()
    clock = now if now is not None else time.monotonic

    mounts_text = mounts.read_text() if mounts.is_file() else ""

    update_mode_devices = _probe_update_mode_devices(block, mounts_text, mounter, clock)

    results = {
        "op1": _probe_op1(block, mounts_text, mounter, clock),
        "spotykach": _probe_spotykach(tty, usb),
        "wave_bard": _probe_staged("wave_bard", staging, update_mode_devices),
        "sp404": _probe_sp404(block, mounts_text, mounter, clock),
        "fx_wizard": _probe_staged("fx_wizard", staging, update_mode_devices),
    }
    return [results[device_id] for device_id in DEVICE_ORDER]


def status_payload(
    block_dir: Path | str = "/sys/block",
    tty_dir: Path | str = "/sys/class/tty",
    usb_dir: Path | str = "/sys/bus/usb/devices",
    mounts_path: Path | str = "/proc/mounts",
    staging_dir: Path | str | None = None,
    mounter: Mounter | None = None,
    now: Callable[[], float] | None = None,
) -> dict:
    """The JSON body for GET /api/status: an ordered device list plus a stamp."""
    return {
        "devices": [
            d.to_dict()
            for d in probe(block_dir, tty_dir, usb_dir, mounts_path, staging_dir, mounter, now)
        ],
        "generated_at": time.time(),
    }
