"""OP-1 drum-kit writer — reuses op1-web's proven AIFC path.

A bare AIFC file is not enough for the OP-1: it lists only kits inside
``drum/user`` that carry the ``FVER`` + ``APPL`` slice metadata (24 pads, each a
32-bit fraction of a 24 s timeline). ``op1_studio.drumkit`` already encodes that
correctly, so this module reuses ``prepare`` + ``build_kit`` verbatim, and
``op1_studio.device.write_drum_kit`` for the atomic, collision-safe placement.

``convert.to_op1_drum`` emits the raw audio container (mono 44.1 kHz 16-bit
AIFC); this module is the authoritative OP-1 drum path because it adds the slice
metadata the device needs to list the kit.

No singleton: each call re-runs the conversion and holds no persistent handle.
The op1-web source is resolved from the sibling repo, overridable with
``OP1_WEB_SRC``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _op1_web():
    """Import op1_studio.drumkit + device from the sibling op1-web repo."""
    src = os.environ.get("OP1_WEB_SRC")
    src_dir = Path(src) if src else (
        Path(__file__).resolve().parents[2].parent / "op1-web" / "src"
    )
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from op1_studio import device, drumkit
    return drumkit, device


drumkit, device = _op1_web()

MAX_SECONDS = drumkit.MAX_SECONDS
MAX_FRAMES = drumkit.MAX_FRAMES
NAME_LIMIT = drumkit.NAME_LIMIT

DeviceError = device.DeviceError
find_mount = device.find_mount


def build_drum_kit(data: bytes, filename: str = "upload.wav") -> tuple[bytes, str]:
    """Canonical WAV -> (full OP-1 drum-kit AIFC bytes, display name)."""
    facts = drumkit.prepare(data, filename)
    payload = drumkit.build_kit(facts["name"], facts["pcm"], facts["frames"])
    return payload, facts["name"]


def write_drum_kit(data, filename="upload.wav", mount=None, force=False):
    """Convert + write one kit into ``drum/user/``; returns the device report."""
    payload, name = build_drum_kit(data, filename)
    return device.write_drum_kit(mount, f"{name}.aif", payload, force=force)


__all__ = [
    "MAX_SECONDS", "MAX_FRAMES", "NAME_LIMIT",
    "DeviceError", "find_mount", "build_drum_kit", "write_drum_kit",
]
