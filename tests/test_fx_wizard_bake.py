"""Milestone 11 — FX Wizard bake: k2fx rhythm binary + merged .uf2.

FX Wizard is rhythm-only (a 9-mode FX processor). Its loader stores a ``k2fx``
user-data binary — a magic header, a rhythm count and sequencer length, then one
u32 ``steps`` bitmask per rhythm — wrapped in UF2 blocks at ``USER_DATA_BEGIN``
and merged onto the base firmware. This mirrors the Chamber loader's
``fxWizardBinaryFormat.js`` + ``uf2Utils.js`` pipeline, ported to Python. No
device I/O, so the bake is fully testable without hardware.
"""

from __future__ import annotations

import struct

import pytest

from server.devices.fx_wizard import build_binary, build_uf2, encode_binary
from server.pattern import FX_WIZARD_DEFAULT_LENGTH, pattern_to_steps
from server.uf2 import UF2_FAMILY_ID, USER_DATA_BEGIN, generate_uf2, validate_uf2

FX_WIZARD_MAGIC = b"k2fx"
FX_WIZARD_END = b"ahoj"


def make_state(length: int = 16) -> dict:
    rhythms = [
        {
            "id": f"r{i}",
            "name": f"rhythm {i}",
            "steps": pattern_to_steps([i % length], length),
        }
        for i in range(3)
    ]
    return {"sequencerLength": length, "rhythms": rhythms}


def _steps(state: dict) -> list[int]:
    return [int(r["steps"]) for r in state["rhythms"]]


def _blocks(uf2: bytes) -> dict[int, bytes]:
    out = {}
    for i in range(0, len(uf2), 512):
        block = uf2[i : i + 512]
        address = struct.unpack_from("<I", block, 12)[0]
        out[address] = block[32 : 32 + 256]
    return out


def test_encode_k2fx_exact_bytes():
    binary = encode_binary([0x00000123, 0x00000456], 16)
    expected = (
        b"k2fx"
        + struct.pack("<I", 20 + 2 * 4 + 4)
        + bytes([2, 16])
        + b"\x00" * 10
        + struct.pack("<II", 0x00000123, 0x00000456)
        + b"ahoj"
    )
    assert binary == expected


def test_encode_k2fx_masks_steps_to_uint32():
    # Bastl writes steps with setUint32, which wraps; a 64-step full mask
    # overflows and must truncate to 32 bits rather than error.
    binary = encode_binary([(1 << 64) - 1], 64)
    assert struct.unpack_from("<I", binary, 20)[0] == 0xFFFFFFFF


def test_build_binary_writes_magic_header_size_and_counts():
    state = make_state()
    binary = build_binary(state)
    assert binary[:4] == FX_WIZARD_MAGIC
    assert binary[-4:] == FX_WIZARD_END
    assert struct.unpack_from("<I", binary, 4)[0] == len(binary)
    assert binary[8] == len(state["rhythms"])
    assert binary[9] == state["sequencerLength"]
    assert binary[10:20] == b"\x00" * 10


def test_build_binary_encodes_rhythms():
    state = make_state()
    binary = build_binary(state)
    steps = _steps(state)
    assert struct.unpack_from("<I", binary, 20)[0] == steps[0]
    assert struct.unpack_from("<I", binary, 24)[0] == steps[1]
    assert struct.unpack_from("<I", binary, 28)[0] == steps[2]


def test_build_binary_defaults_sequencer_length():
    state = make_state()
    del state["sequencerLength"]
    binary = build_binary(state)
    assert binary[9] == FX_WIZARD_DEFAULT_LENGTH


def test_build_binary_rejects_invalid_state():
    with pytest.raises(ValueError):
        build_binary({"sequencerLength": 8, "rhythms": []})


def test_build_uf2_merges_base_and_rhythms_at_user_data_begin():
    state = make_state()
    base = generate_uf2(UF2_FAMILY_ID, 0x1007FF00, b"\xBB" * 256)
    merged = build_uf2(state, base)
    assert validate_uf2(merged)
    blocks = _blocks(merged)
    assert blocks[0x1007FF00] == b"\xBB" * 256
    assert blocks[USER_DATA_BEGIN][:4] == FX_WIZARD_MAGIC


def test_build_uf2_fills_gap_between_program_and_rhythms():
    state = make_state()
    base = generate_uf2(UF2_FAMILY_ID, 0x1007FD00, b"\xBB" * 256)
    merged = build_uf2(state, base)
    assert validate_uf2(merged)
    blocks = _blocks(merged)
    assert blocks[0x1007FE00] == b"\x00" * 256
    assert blocks[USER_DATA_BEGIN][:4] == FX_WIZARD_MAGIC
