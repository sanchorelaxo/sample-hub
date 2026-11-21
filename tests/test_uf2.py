"""UF2 read/generate/validate/merge — byte-exact port of Bastl uf2Utils.js.

The Kastle 2 (RP2040) exposes its flash as a UF2 mass-storage device. A UF2
file is a sequence of 512-byte blocks, each carrying a 256-byte payload
addressed into flash. These helpers reproduce the client-side logic
apps.bastl-instruments.com uses to build a custom firmware image, so sample-hub
can emit a flashable .uf2 locally with no external handoff.
"""

from __future__ import annotations

import struct

import pytest

from server.uf2 import (
    BLOCK_SIZE,
    PAYLOAD_SIZE,
    UF2_FAMILY_ID,
    UF2_MAGIC_END,
    UF2_MAGIC_START0,
    UF2_MAGIC_START1,
    generate_uf2,
    merge_uf2,
    read_uf2,
    validate_uf2,
)


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def test_generate_uf2_block_layout():
    data = bytes(range(256))  # exactly one block
    uf2 = generate_uf2(UF2_FAMILY_ID, 0x10080000, data)
    assert len(uf2) == BLOCK_SIZE
    assert u32(uf2, 0) == UF2_MAGIC_START0
    assert u32(uf2, 4) == UF2_MAGIC_START1
    assert u32(uf2, 8) == 0x00002000  # flags
    assert u32(uf2, 12) == 0x10080000  # target address
    assert u32(uf2, 16) == PAYLOAD_SIZE
    assert u32(uf2, 20) == 0  # block number
    assert u32(uf2, 24) == 1  # total blocks
    assert u32(uf2, 28) == UF2_FAMILY_ID
    assert uf2[32 : 32 + PAYLOAD_SIZE] == data
    assert u32(uf2, BLOCK_SIZE - 4) == UF2_MAGIC_END


def test_generate_uf2_pads_last_block():
    data = b"\x01\x02\x03"
    uf2 = generate_uf2(UF2_FAMILY_ID, 0x10080000, data)
    assert len(uf2) == BLOCK_SIZE
    assert uf2[32:35] == data
    assert uf2[35 : 32 + PAYLOAD_SIZE] == b"\x00" * (PAYLOAD_SIZE - 3)


def test_generate_uf2_spans_multiple_blocks():
    data = bytes(600)  # ceil(600/256) == 3 blocks
    uf2 = generate_uf2(UF2_FAMILY_ID, 0x10080000, data)
    assert len(uf2) == 3 * BLOCK_SIZE
    for i in range(3):
        off = i * BLOCK_SIZE
        assert u32(uf2, off + 20) == i  # block number
        assert u32(uf2, off + 24) == 3  # total blocks
        assert u32(uf2, off + 12) == 0x10080000 + i * PAYLOAD_SIZE


def test_read_uf2_recovers_binary():
    data = bytes(range(256))
    uf2 = generate_uf2(UF2_FAMILY_ID, 0x10080000, data)
    assert read_uf2(uf2) == data


def test_read_uf2_rejects_bad_family():
    uf2 = generate_uf2(0xDEADBEEF, 0x10080000, bytes(256))
    assert read_uf2(uf2) is None


def test_validate_uf2_accepts_generated():
    uf2 = generate_uf2(UF2_FAMILY_ID, 0x10080000, bytes(300))
    assert validate_uf2(uf2) is True


def test_validate_uf2_rejects_corrupted_magic():
    uf2 = bytearray(generate_uf2(UF2_FAMILY_ID, 0x10080000, bytes(256)))
    uf2[0] = 0x00
    assert validate_uf2(bytes(uf2)) is False


def test_merge_uf2_fills_gaps_and_renumbers():
    base = 0x10000000
    a = generate_uf2(UF2_FAMILY_ID, base, b"A" * 256)
    b = generate_uf2(UF2_FAMILY_ID, base + 3 * 256, b"B" * 256)
    merged = merge_uf2([a, b], fill_gaps=True)
    # a@base, empty@base+256, empty@base+512, b@base+768 -> 4 blocks
    assert len(merged) == 4 * BLOCK_SIZE
    for i in range(4):
        off = i * BLOCK_SIZE
        assert u32(merged, off + 20) == i  # renumbered block number
        assert u32(merged, off + 24) == 4  # total block count
    assert u32(merged, 12) == base
    assert merged[32 : 32 + 256] == b"A" * 256
    assert u32(merged, 3 * BLOCK_SIZE + 12) == base + 3 * 256
    assert merged[3 * BLOCK_SIZE + 32 : 3 * BLOCK_SIZE + 32 + 256] == b"B" * 256
    # gap blocks carry zero payloads and valid family id
    assert merged[BLOCK_SIZE + 32 : BLOCK_SIZE + 32 + 256] == b"\x00" * 256
    assert u32(merged, BLOCK_SIZE + 28) == UF2_FAMILY_ID
    # round-trip recovers the full binary including the gap
    assert read_uf2(merged) == b"A" * 256 + b"\x00" * (2 * 256) + b"B" * 256


def test_merge_uf2_no_gap_fill_concatenates_sorted():
    base = 0x10000000
    b = generate_uf2(UF2_FAMILY_ID, base + 256, b"B" * 256)
    a = generate_uf2(UF2_FAMILY_ID, base, b"A" * 256)
    merged = merge_uf2([b, a], fill_gaps=False)
    assert len(merged) == 2 * BLOCK_SIZE
    # sorted by address: a first, then b
    assert u32(merged, 12) == base
    assert u32(merged, BLOCK_SIZE + 12) == base + 256
