"""SP-404MK2 writer — stage 16-bit 48 kHz WAV into the SD card's IMPORT/ folder.

The MK2 does not expose its SD card as USB mass storage; it uses a custom
SP404REMOTE protocol (see the SparkyMK2 reference). In v1 this module writes to
an already-mounted card root (a card reader, or a future SparkyMK2 bridge),
placing a 16-bit 48 kHz WAV in ``IMPORT/`` where the unit's own IMPORT browser
finds it (SHIFT+pad13 -> IMPORT from SD-CARD). The transfer is two-hop: the app
stages the file, the unit-side import is manual.

No singleton: each call converts and writes fresh, holding no persistent handle.
"""
from __future__ import annotations

import os
from pathlib import Path

from server.convert import to_sp404


def _safe_name(filename: str) -> str:
    """Sanitize a source filename into an IMPORT-safe ``<stem>.wav`` leaf."""
    stem = Path(filename).stem
    kept = "".join(ch if (ch.isalnum() or ch in " -_.") else "_" for ch in stem)
    stem = kept.strip(" .") or "sample"
    if len(stem) > 40:
        stem = stem[:40].rstrip(" .")
    return f"{stem}.wav"


def _next_free(import_dir: Path, name: str) -> Path:
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(2, 1000):
        candidate = import_dir / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"IMPORT/ exhausted for {name}")


def stage_import(data: bytes, filename: str = "upload.wav",
                 card_root=None, force: bool = False) -> dict:
    """Canonical WAV -> 16-bit 48 kHz WAV staged in ``IMPORT/`` on the card.

    Returns ``{"rel": "IMPORT/<name>.wav", "bytes": N, "name": "<name>.wav"}``.
    Refuses to overwrite unless ``force``; writes atomically via ``os.replace``.
    """
    if card_root is None:
        raise ValueError("card_root is required (SP-404 SD is not auto-mounted)")
    wav = to_sp404(data)
    import_dir = Path(card_root) / "IMPORT"
    import_dir.mkdir(parents=True, exist_ok=True)

    name = _safe_name(filename)
    dest = import_dir / name
    if dest.exists() and not force:
        dest = _next_free(import_dir, name)

    part = dest.with_name(dest.name + ".part")
    part.write_bytes(wav)
    os.replace(part, dest)

    return {"rel": f"IMPORT/{dest.name}", "bytes": len(wav), "name": dest.name}


__all__ = ["stage_import", "_safe_name"]
