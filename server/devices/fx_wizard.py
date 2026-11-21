"""FX Wizard stager + baker — rhythm set draft and ``k2fx`` ``.uf2``.

FX Wizard is a rhythm-only destination (a 9-mode FX processor); it never stores
audio samples. ``build_draft`` takes the full editor state (sequencer length plus
a list of rhythms, each a ``steps`` bitmask — see server.pattern) and emits a
"ready to flash" Chamber draft. ``build_binary``/``build_uf2`` bake that state
into the Chamber loader's ``k2fx`` container and wrap it in a UF2 image merged
onto the base firmware (firmware 1.6+). No device I/O, so the draft and bake are
fully testable without hardware.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from server.devices import READY_TO_FLASH
from server.pattern import FX_WIZARD_DEFAULT_LENGTH, validate_fx_wizard
from server.uf2 import UF2_FAMILY_ID, USER_DATA_BEGIN, generate_uf2, merge_uf2

# Bastl's fxWizardBinaryFormat.js container constants (port of the shared
# FirmwareBinaryBase helpers it extends).
FX_WIZARD_MAGIC = b"k2fx"
FX_WIZARD_END = b"ahoj"
FX_WIZARD_HEADER_SIZE = 20


def build_draft(state: dict, out_dir=None) -> dict:
    """Full editor state -> a "ready to flash" Chamber rhythm-set draft.

    ``state`` carries ``sequencerLength`` and ``rhythms`` (a list of
    ``{"id"?, "name", "steps"}``). Raises ``ValueError`` when the rhythm set
    fails the loader's 3..64 limits. With ``out_dir`` the draft is also written
    to ``<out_dir>/manifest.json``.
    """
    rhythms = state.get("rhythms", [])
    err = validate_fx_wizard(rhythms)
    if err:
        raise ValueError(err)

    rhythms = [
        {
            "id": r.get("id") or f"r{i + 1}",
            "name": r.get("name", f"rhythm {i + 1}"),
            "steps": int(r["steps"]),
        }
        for i, r in enumerate(rhythms)
    ]

    draft = {
        "state": READY_TO_FLASH,
        "target": "fx-wizard",
        "format": "chamber",
        "sequencerLength": int(state.get("sequencerLength", FX_WIZARD_DEFAULT_LENGTH)),
        "rhythms": rhythms,
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True))
        draft["manifest_path"] = str(manifest_path)
    return draft


__all__ = ["build_draft", "build_binary", "build_uf2", "encode_binary"]


def _u32_le(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def encode_binary(rhythms: list[int], sequencer_length: int) -> bytes:
    """Assemble the ``k2fx`` user-data binary (port of FxWizardBinaryWriter).

    ``rhythms`` is the list of u32 ``steps`` bitmasks and ``sequencer_length``
    is stored in the header. Layout: magic ``k2fx`` (4) + file size u32 LE (4) +
    rhythm count u8 + sequencer length u8 + zero padding to a 20-byte header,
    then one u32 LE bitmask per rhythm, then the 4-byte ``ahoj`` end marker.
    """
    total = FX_WIZARD_HEADER_SIZE + 4 * len(rhythms) + 4
    out = bytearray()
    out += FX_WIZARD_MAGIC
    out += _u32_le(total)
    out += bytes([len(rhythms) & 0xFF, sequencer_length & 0xFF])
    out += b"\x00" * (FX_WIZARD_HEADER_SIZE - len(out))
    for rhythm in rhythms:
        out += _u32_le(rhythm)
    out += FX_WIZARD_END
    return bytes(out)


def build_binary(state: dict) -> bytes:
    """Full editor state -> the ``k2fx`` user-data binary (rhythms only)."""
    rhythms = state.get("rhythms", [])
    err = validate_fx_wizard(rhythms)
    if err:
        raise ValueError(err)
    sequencer_length = int(state.get("sequencerLength", FX_WIZARD_DEFAULT_LENGTH))
    steps = [int(r["steps"]) for r in rhythms]
    return encode_binary(steps, sequencer_length)


def build_uf2(state: dict, base_firmware: bytes) -> bytes:
    """Editor state -> a merged, validated ``.uf2`` image.

    Mirrors the Chamber loader's pipeline: build the ``k2fx`` binary, wrap it in
    UF2 blocks at ``USER_DATA_BEGIN``, then merge onto ``base_firmware`` (a Bastl
    ``kastle2-fx-wizard-*.uf2``) with ``fill_gaps=True`` so the flash range is
    mapped contiguously. Returns the full image bytes; nothing is written to
    hardware.
    """
    rhythms_uf2 = generate_uf2(UF2_FAMILY_ID, USER_DATA_BEGIN, build_binary(state))
    return merge_uf2([base_firmware, rhythms_uf2], fill_gaps=True)
