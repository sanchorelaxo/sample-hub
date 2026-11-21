"""Flashable-device mount detection + UF2 staging (Bastl sinks).

Wave Bard and FX Wizard are ``is_flashable``: their firmware is replaced by
copying a ``.uf2`` onto the board while it is in boot mode, when it enumerates
as an RP2040 UF2 mass-storage volume (label ``RPI-RP2``) carrying an
``INFO_UF2.TXT`` that reports the bootloader and board id. ``find_boot_volumes``
locates those volumes without writing; ``stage_uf2`` plans or performs the
write, gated behind ``dry_run`` because copying a ``.uf2`` onto a volume flashes
and resets the unit.
"""

from __future__ import annotations

import os
from pathlib import Path

from server.uf2 import BLOCK_SIZE, validate_uf2

SEARCH_ROOTS = (
    Path("/media"),
    Path("/run/media"),
    Path("/mnt"),
    Path("/Volumes"),  # macOS
)

INFO_FILE = "INFO_UF2.TXT"
BOOT_SIGNATURE = "UF2 Bootloader"
BOARD_ID = "RPI-RP2"


def _is_boot_volume(path: Path) -> bool:
    info = path / INFO_FILE
    try:
        if not info.is_file():
            return False
        text = info.read_text()
    except OSError:
        # Unreadable mount (e.g. /media/root owned by root) — not a boot volume.
        return False
    return BOOT_SIGNATURE in text and BOARD_ID in text


def _candidates() -> list[Path]:
    found: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        try:
            children = [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            found.append(child)
            # /media/<user>/<label> nests one level deeper than /run/media/<user>.
            try:
                found.extend(c for c in child.iterdir() if c.is_dir())
            except OSError:
                pass
    return found


def find_boot_volumes() -> list[Path]:
    """Mounted RP2040 UF2 boot volumes, sorted by path and de-duplicated."""
    volumes: list[Path] = []
    seen: set[str] = set()
    for candidate in _candidates():
        if not _is_boot_volume(candidate):
            continue
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        volumes.append(candidate)
    return sorted(volumes, key=str)


class FlashError(ValueError):
    """Raised when a firmware write cannot proceed (bad image, target, or name)."""


def stage_uf2(volume: Path, data: bytes, filename: str = "firmware.uf2",
              *, dry_run: bool = False) -> dict:
    """Validate and (optionally) write a ``.uf2`` image to an RP2040 boot volume.

    Copying a ``.uf2`` onto a boot volume flashes and resets the unit, so the
    write runs only when ``dry_run`` is False. A dry run validates the image,
    the target volume and the filename, then returns the plan it would execute
    without touching the volume. The caller is expected to pass the single boot
    volume discovered by ``find_boot_volumes``.
    """
    if not validate_uf2(data):
        raise FlashError("data is not a valid UF2 image for this family")
    volume = Path(volume)
    if not _is_boot_volume(volume):
        raise FlashError(f"{volume} is not an RP2040 UF2 boot volume")
    leaf = Path(filename).name
    if leaf != filename or not leaf.endswith(".uf2"):
        raise FlashError("filename must be a simple <name>.uf2 leaf")

    plan = {
        "dry_run": dry_run,
        "volume": str(volume),
        "filename": leaf,
        "bytes": len(data),
        "blocks": len(data) // BLOCK_SIZE,
    }
    if dry_run:
        return plan

    dest = volume / leaf
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    os.replace(part, dest)
    return plan


__all__ = ["find_boot_volumes", "stage_uf2", "FlashError"]
