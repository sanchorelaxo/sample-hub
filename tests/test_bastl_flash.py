"""Bastl firmware flash — RP2040 UF2 boot-volume detection (real-device gated).

Wave Bard and FX Wizard are ``is_flashable``: in firmware (boot) mode each
enumerates as an RP2040 UF2 mass-storage volume labelled ``RPI-RP2`` whose
``INFO_UF2.TXT`` reports the bootloader and board id.

The front-end workflow flashes one instrument at a time and asks the user to
connect only that one device, so discovery just has to find and validate the
connected boot volume(s) — there is no need to tell two devices apart. This
test is read-only: it never copies a ``.uf2`` onto a volume, because doing so
flashes and resets the unit. The byte-level UF2 format is covered device-free
in ``test_uf2.py``.
"""

from __future__ import annotations

import pytest

from server.devices.flashable import find_boot_volumes


@pytest.mark.real_devices
def test_bastl_boot_volume_detected():
    volumes = find_boot_volumes()
    if not volumes:
        pytest.skip("no Bastl device in firmware (UF2 boot) mode")
    for volume in volumes:
        info = (volume / "INFO_UF2.TXT").read_text()
        assert "UF2 Bootloader" in info
        assert "RPI-RP2" in info
