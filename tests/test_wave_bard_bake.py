"""Milestone 7 — Wave Bard bake: editor state -> k2wb -> merged .uf2.

Ports Bastl's ``waveBardBinaryFormat.js`` (magic ``k2wb``, end marker ``ahoj``,
20-byte header, Int16 sample data) and the loader's firmware-generation pipeline
(``generateUf2(USER_DATA_BEGIN)`` + ``mergeUf2([base, samples], fill_gaps)``).

Device-free: feeds ffmpeg-generated canonical WAVs and a synthetic base firmware,
then decodes the result with ``struct`` — no mocks.
"""

from __future__ import annotations

import struct
import subprocess

import pytest

from server.devices.wave_bard import build_binary, build_uf2, encode_binary
from server.pattern import pattern_to_steps
from server.uf2 import UF2_FAMILY_ID, USER_DATA_BEGIN, generate_uf2, validate_uf2

FFMPEG = "ffmpeg"


def run_ffmpeg(args: list[str], stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", *args],
        input=stdin,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout


def canonical_wav(seconds: float = 1.0, freq: float = 440.0) -> bytes:
    return run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ar", "48000", "-ac", "2",
            "-c:a", "pcm_f32le", "-f", "wav", "-",
        ]
    )


def make_state() -> dict:
    return {
        "sampleRate": 44100,
        "audioProcessing": {"fadeIn": True, "fadeOut": False, "trimQuiet": False, "maximize": False},
        "sequencerLength": 16,
        "banks": [
            {
                "id": "b0",
                "name": "bank 0",
                "samples": [
                    {"id": "b0s0", "name": "kick00", "data": canonical_wav()},
                    {"id": "b0s1", "name": "kick01", "data": canonical_wav(freq=220.0)},
                    {"id": "b0s2", "name": "kick02", "data": canonical_wav(freq=880.0)},
                ],
            }
        ],
        "scales": [
            {"id": "sc0", "name": "scale 0", "semitones": 0b101011010101},
            {"id": "sc1", "name": "scale 1", "semitones": 0b1},
            {"id": "sc2", "name": "scale 2", "semitones": 0b10},
        ],
        "rhythms": [
            {"id": "r0", "name": "rhythm 0", "steps": pattern_to_steps([0, 3, 7], 16)},
            {"id": "r1", "name": "rhythm 1", "steps": pattern_to_steps([1], 16)},
            {"id": "r2", "name": "rhythm 2", "steps": pattern_to_steps([2], 16)},
        ],
    }


def _blocks(uf2: bytes) -> dict[int, bytes]:
    return {
        struct.unpack_from("<I", uf2, off + 12)[0]: uf2[off + 32 : off + 32 + 256]
        for off in range(0, len(uf2), 512)
    }


# ---- k2wb byte-level writer --------------------------------------------

def test_encode_k2wb_exact_bytes():
    """The writer reproduces the loader's exact k2wb layout, byte for byte."""
    got = encode_binary(
        [{"label": "bk", "color": "#010203", "samples": [{"label": "sm", "channels": 1, "data": struct.pack("<4h", 1, -2, 3, -4)}]}],
        [0x123],
        [0x456],
        16,
        44100,
    )
    expected = (
        b"k2wb"
        + struct.pack("<II", 68, 44100)          # file size, sample rate
        + bytes([16, 1, 1, 1, 1, 16, 0, 0])      # bitDepth, banks, samples/bank, scales, rhythms, seq len, 2 reserved
        + struct.pack("<I", 0x123)               # scales
        + struct.pack("<I", 0x456)               # rhythms
        + b"bk\x00\x00\x00\x00\x00\x00"          # bank label (8 bytes)
        + bytes([0x01, 0x02, 0x03, 0x00])        # color RGB + reserved
        + struct.pack("<I", 8)                   # sample data size (8 bytes, no padding)
        + bytes([1])                             # channels
        + b"sm\x00\x00\x00\x00\x00\x00"          # sample label (8 bytes)
        + bytes([0, 0, 0])                       # 3 reserved
        + struct.pack("<4h", 1, -2, 3, -4)       # int16 LE samples
        + b"ahoj"                                # end marker
    )
    assert got == expected


def test_encode_k2wb_pads_sample_data_to_4_bytes():
    """A 6-byte (3-sample) payload gets 2 padding bytes; header reports size+pad."""
    got = encode_binary(
        [{"label": "b", "color": "#FFFFFF", "samples": [{"label": "s", "channels": 1, "data": struct.pack("<3h", 1, 2, 3)}]}],
        [0x1], [0x2], 8, 22050,
    )
    # sample header starts at 20 + 4 + 4 = 28; bank header 12 -> sample header at 40
    sample_header = 20 + 4 + 4 + 12
    size_field = struct.unpack_from("<I", got, sample_header)[0]
    assert size_field == 8  # 6 bytes data + 2 padding
    # the two padding bytes right after the 6 sample bytes are zero
    assert got[sample_header + 16 + 6 : sample_header + 16 + 8] == b"\x00\x00"


def test_encode_k2wb_truncates_labels_to_8_bytes():
    got = encode_binary(
        [{"label": "this-is-a-very-long-bank-name", "color": "#FFFFFF", "samples": [{"label": "a-long-sample-label-too", "channels": 1, "data": b"\x00\x00"}]}],
        [0x1], [0x2], 8, 44100,
    )
    assert got[20 + 4 + 4 : 20 + 4 + 4 + 8] == b"this-is-"  # first 8 chars


def test_encode_k2wb_masks_steps_to_uint32():
    """Rhythm steps mirror the loader's setUint32 (32-bit wrap)."""
    got = encode_binary([], [0x1], [0x1_0000_0001], 8, 44100)  # high bit set
    rhythm_off = 20 + 4  # after header + one scale
    assert struct.unpack_from("<I", got, rhythm_off)[0] == 1  # wrapped


# ---- full editor state -> k2wb -----------------------------------------

def test_build_binary_writes_magic_header_and_size():
    binary = build_binary(make_state())
    assert binary[:4] == b"k2wb"
    assert binary[-4:] == b"ahoj"
    assert struct.unpack_from("<I", binary, 4)[0] == len(binary)
    assert struct.unpack_from("<I", binary, 8)[0] == 44100  # sample rate
    assert binary[12:20] == bytes([16, 1, 3, 3, 3, 16, 0, 0])  # bitDepth, 1 bank, 3 samples, 3 scales, 3 rhythms, seq 16


def test_build_binary_encodes_scales_then_rhythms():
    binary = build_binary(make_state())
    # header(20) -> scales(3 * 4) -> rhythms(3 * 4)
    assert struct.unpack_from("<I", binary, 20)[0] == 0b101011010101
    assert struct.unpack_from("<I", binary, 32)[0] == pattern_to_steps([0, 3, 7], 16)
    assert struct.unpack_from("<I", binary, 36)[0] == pattern_to_steps([1], 16)


def test_build_binary_sample_data_is_int16_and_nonzero():
    binary = build_binary(make_state())
    # header(20) + scales(12) + rhythms(12), then 12-byte bank header
    sample_header = 20 + 12 + 12 + 12
    size_field = struct.unpack_from("<I", binary, sample_header)[0]
    channels = binary[sample_header + 4]
    assert channels == 1
    sample_count = (size_field - (4 - size_field % 4) % 4) // 2
    data = binary[sample_header + 16 : sample_header + 16 + sample_count * 2]
    samples = struct.unpack(f"<{sample_count}h", data)
    assert len(samples) == sample_count
    assert max(abs(s) for s in samples) > 1000  # a 1s 440Hz sine, not silence


def test_build_binary_rejects_invalid_state():
    state = make_state()
    state["banks"] = []
    with pytest.raises(ValueError):
        build_binary(state)


# ---- editor state -> merged .uf2 ---------------------------------------

def test_build_uf2_merges_base_and_samples_at_user_data_begin():
    state = make_state()
    base = generate_uf2(UF2_FAMILY_ID, 0x1007FF00, b"\xBB" * 256)  # program ending at USER_DATA_BEGIN
    merged = build_uf2(state, base)
    assert validate_uf2(merged)
    blocks = _blocks(merged)
    assert 0x1007FF00 in blocks                    # base program preserved
    assert 0x10080000 in blocks                    # == USER_DATA_BEGIN
    assert blocks[0x10080000][:4] == b"k2wb"       # samples start here


def test_build_uf2_fills_gap_between_program_and_samples():
    state = make_state()
    base = generate_uf2(UF2_FAMILY_ID, 0x1007FD00, b"\xBB" * 256)  # 512-byte gap below USER_DATA_BEGIN
    merged = build_uf2(state, base)
    assert validate_uf2(merged)
    blocks = _blocks(merged)
    assert blocks[0x1007FE00] == b"\x00" * 256     # gap zero-filled
    assert blocks[0x10080000][:4] == b"k2wb"
