"""Milestone 8 — the status probe: one verdict per instrument, green/amber/red.

SPEC.md §5.1 wants a presence dot on every pane before a drop is allowed:
green = the instrument is present and its transport is usable now; amber =
present but blocked on a user step (needs a mount, a flash, a direct port);
red = absent. The probe reads only sysfs and /proc/mounts, so it never needs
the device writers imported and never touches a live unit unless a transport
is genuinely there.

Every sysfs root is an explicit argument so the real-device tests and this
module share one read-only entry point and unit tests can point it at a
synthetic tree instead of the machine.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from server.devices import READY_TO_FLASH

GREEN = "green"
AMBER = "amber"
RED = "red"

# The pane order the client renders (SPEC.md §5.1), left to right.
DEVICE_ORDER = ("op1", "spotykach", "wave_bard", "sp404", "fx_wizard")

LABELS = {
    "op1": "OP-1",
    "spotykach": "Spotykach",
    "wave_bard": "Wave Bard",
    "sp404": "SP-404MK2",
    "fx_wizard": "FX Wizard",
}

# idVendor of the Linux root hub; downstream hubs share bDeviceClass 09 but a
# different vendor, so the "on a hub" check ignores this one.
ROOT_HUB_VENDOR = "1d6b"
HUB_CLASS = "09"

# Spotykach enumerates as a USB CDC ACM port, but so does the SP-404MK2, so a
# bare ttyACM match is not enough: match the Spotykach's VID/PID instead.
SPOTYKACH_VENDOR = 0x0483
SPOTYKACH_PRODUCT = 0x5740


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


def _probe_op1(block_dir: Path, mounts_text: str) -> DeviceStatus:
    for block in _block_devices(block_dir):
        if not _is_op1(block):
            continue
        mount = _mount_for(mounts_text, block["name"])
        if mount:
            return DeviceStatus("op1", LABELS["op1"], "disk", GREEN, True, mount)
        return DeviceStatus(
            "op1",
            LABELS["op1"],
            "disk",
            AMBER,
            True,
            f"detected /dev/{block['name']}, not mounted",
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


def _probe_spotykach(tty_dir: Path) -> DeviceStatus:
    ports: list[str] = []
    if tty_dir.is_dir():
        for sys_path in sorted(tty_dir.glob("ttyACM*")):
            if _usb_ids(sys_path) == (SPOTYKACH_VENDOR, SPOTYKACH_PRODUCT):
                ports.append(f"/dev/{sys_path.name}")
    if ports:
        return DeviceStatus(
            "spotykach", LABELS["spotykach"], "usb_cdc", GREEN, True, ", ".join(ports)
        )
    return DeviceStatus(
        "spotykach", LABELS["spotykach"], "usb_cdc", RED, False, "no Spotykach on /dev/ttyACM*"
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


def _probe_sp404(block_dir: Path, mounts_text: str) -> DeviceStatus:
    # The MK2's SD is read through a host card reader, not USB mass storage, so
    # presence is the card's SP-404 signature: a top-level IMPORT/ folder. That
    # keeps a plain media SD card (mounted, but no IMPORT/) from reading green.
    for block in _block_devices(block_dir):
        if _is_op1(block):
            continue
        is_sd_card = block["name"].startswith("mmcblk") or block["removable"]
        if not is_sd_card:
            continue
        mount = _mount_for(mounts_text, block["name"])
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


def _probe_staged(device_id: str, staging_dir: Path) -> DeviceStatus:
    """A staged-firmware instrument is amber only while a draft waits to flash."""
    manifest = staging_dir / device_id / "manifest.json"
    if manifest.is_file():
        try:
            state = json.loads(manifest.read_text()).get("state")
        except (OSError, json.JSONDecodeError):
            state = None
        detail = "ready to flash" if state == READY_TO_FLASH else "draft staged"
        return DeviceStatus(
            device_id, LABELS[device_id], "staged_firmware", AMBER, True, detail
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
) -> list[DeviceStatus]:
    """One pass over the live hardware (or an injected tree) — read-only."""
    block = Path(block_dir)
    tty = Path(tty_dir)
    usb = Path(usb_dir)
    mounts = Path(mounts_path)
    staging = Path(staging_dir) if staging_dir is not None else default_staging_dir()

    mounts_text = mounts.read_text() if mounts.is_file() else ""

    results = {
        "op1": _probe_op1(block, mounts_text),
        "spotykach": _probe_spotykach(tty),
        "wave_bard": _probe_staged("wave_bard", staging),
        "sp404": _probe_sp404(block, mounts_text),
        "fx_wizard": _probe_staged("fx_wizard", staging),
    }
    return [results[device_id] for device_id in DEVICE_ORDER]


def status_payload(
    block_dir: Path | str = "/sys/block",
    tty_dir: Path | str = "/sys/class/tty",
    usb_dir: Path | str = "/sys/bus/usb/devices",
    mounts_path: Path | str = "/proc/mounts",
    staging_dir: Path | str | None = None,
) -> dict:
    """The JSON body for GET /api/status: an ordered device list plus a stamp."""
    return {
        "devices": [d.to_dict() for d in probe(block_dir, tty_dir, usb_dir, mounts_path, staging_dir)],
        "generated_at": time.time(),
    }
