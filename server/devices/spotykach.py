"""Spotykach transfer writer — wraps the spottytube transport.

The conversion matrix's Spotykach row emits a 48 kHz stereo float32 WAV (tag
0x0003), which is exactly the device's ``DEVICE_FORMAT``. This module validates
those bytes against that format, then hands them to the device through the
existing transport (``tools/sk_usb.py`` + ``tools/sk_install.py``).

No singleton: every ``write_sample`` opens its own transport and closes it, so
parallel jobs on other instruments never share a link. The transport is USB
transfer only: the unit's SD card is never mounted into the computer, so there
is no card-fallback path. (USB transfer is a different mode from USB MIDI.)

The transport source dir is resolved from the sibling ``spottytube-ext`` repo,
overridable with ``SPOTTYTUBE_TOOLS`` (mirrors ``sk_usb``'s own ``SPOTTY_USB_PORT``
override used by its pty tests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _spottytube():
    """Import sk_usb + sk_install from spottytube-ext/tools (env-overridable)."""
    tools = os.environ.get("SPOTTYTUBE_TOOLS")
    tools_dir = Path(tools) if tools else (
        Path(__file__).resolve().parents[2].parent / "spottytube-ext" / "tools"
    )
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import sk_usb
    import sk_install
    return sk_usb, sk_install


# Eager import + re-export so the status probe (M8) and real-device tests share
# one import path. The writer genuinely depends on the sibling repo.
sk_usb, sk_install = _spottytube()

DEVICE_FORMAT = sk_usb.DEVICE_FORMAT
TAPE_ORDER = sk_install.TAPE_ORDER
SLOTS_PER_TAPE = sk_install.SLOTS_PER_TAPE

DeviceNotFound = sk_usb.DeviceNotFound
DeviceError = sk_usb.DeviceError
TransferTimeout = sk_usb.TransferTimeout
TransferDropped = sk_usb.TransferDropped
SkLink = sk_usb.SkLink
find_serial_port = sk_usb.find_serial_port
find_midi_port = sk_usb.find_midi_port
enter_transfer_mode = sk_usb.enter_transfer_mode
check_uploadable = sk_usb.check_uploadable
NoTransport = sk_install.NoTransport
InstallError = sk_install.InstallError


def validate(data: bytes, info=None) -> list[str]:
    """Why the device would refuse these WAV bytes; empty list means uploadable."""
    return check_uploadable(data, info)


def write_sample(data, tape, preferred="usb", card_root=None, archive_dir=None):
    """Upload canonical WAV bytes into the first empty Spotykach slot (append).

    Validates the bytes against the device format and the tape name *before*
    opening any transport, then places the sample through ``install_sample`` in
    append mode: the first empty slot of the ``B G P R T Y`` chain starting at
    ``tape``, nothing shifted. Returns the placement as a dict
    (transport/tape/slot/shifted/archived/detail).
    """
    problems = check_uploadable(data, None)
    if problems:
        raise ValueError("Spotykach can't load this file: " + " ".join(problems))
    if tape not in TAPE_ORDER:
        raise ValueError(f"unknown tape {tape!r} (tapes are {TAPE_ORDER})")
    transport = sk_install.open_transport(preferred, card_root=card_root, archive_dir=archive_dir)
    try:
        placement = sk_install.install_sample(
            transport, tape, data, archive_dir=archive_dir, mode="append"
        )
    finally:
        transport.close()
    return placement.as_dict()


__all__ = [
    "DEVICE_FORMAT", "TAPE_ORDER", "SLOTS_PER_TAPE",
    "DeviceNotFound", "DeviceError", "TransferTimeout", "TransferDropped",
    "SkLink", "find_serial_port", "find_midi_port", "enter_transfer_mode",
    "check_uploadable", "NoTransport", "InstallError",
    "validate", "write_sample",
]
