"""UF2 read/generate/validate/merge — byte-exact port of Bastl uf2Utils.js.

The Kastle 2 (RP2040) exposes its flash as a UF2 mass-storage device. A UF2
file is a sequence of 512-byte blocks, each carrying a 256-byte payload
addressed into flash. These helpers reproduce the client-side logic
apps.bastl-instruments.com uses to build a custom firmware image, so sample-hub
can emit a flashable .uf2 locally with no external handoff.
"""

from __future__ import annotations

import struct

BLOCK_SIZE = 512
PAYLOAD_SIZE = 256

UF2_MAGIC_START0 = 0x0A324655  # "UF2\n"
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30

UF2_FAMILY_ID = 0xE48BFF56  # RP2040 (Kastle 2)
UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000

# Flash layout from Bastl's shared/bastl-react/utils/uf2Utils.js: the base
# firmware lives below USER_DATA_BEGIN; custom sample/rhythm/scales data is
# written at USER_DATA_BEGIN and must stay within PROGRAM_MAX_SIZE bytes.
USER_DATA_BEGIN = 0x10080000
PROGRAM_MAX_SIZE = 0x00080000


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _block(family_id: int, address: int, payload: bytes, block_no: int, num_blocks: int) -> bytes:
    """Build one 512-byte UF2 block carrying up to PAYLOAD_SIZE bytes."""
    chunk = payload[:PAYLOAD_SIZE].ljust(PAYLOAD_SIZE, b"\x00")
    block = bytearray(BLOCK_SIZE)
    struct.pack_into(
        "<IIIIIIII",
        block,
        0,
        UF2_MAGIC_START0,
        UF2_MAGIC_START1,
        UF2_FLAG_FAMILY_ID_PRESENT,
        address,
        PAYLOAD_SIZE,
        block_no,
        num_blocks,
        family_id,
    )
    block[32 : 32 + PAYLOAD_SIZE] = chunk
    struct.pack_into("<I", block, BLOCK_SIZE - 4, UF2_MAGIC_END)
    return bytes(block)


def generate_uf2(family_id: int, address: int, data: bytes) -> bytes:
    """Serialize ``data`` as a sequence of UF2 blocks starting at ``address``."""
    num_blocks = max(1, (len(data) + PAYLOAD_SIZE - 1) // PAYLOAD_SIZE)
    out = bytearray()
    for i in range(num_blocks):
        chunk = data[i * PAYLOAD_SIZE : (i + 1) * PAYLOAD_SIZE]
        out += _block(family_id, address + i * PAYLOAD_SIZE, chunk, i, num_blocks)
    return bytes(out)


def validate_uf2(uf2: bytes) -> bool:
    """Return True if ``uf2`` is structurally valid and matches our family id."""
    if not uf2 or len(uf2) % BLOCK_SIZE != 0:
        return False
    for off in range(0, len(uf2), BLOCK_SIZE):
        if _u32(uf2, off) != UF2_MAGIC_START0:
            return False
        if _u32(uf2, off + 4) != UF2_MAGIC_START1:
            return False
        if _u32(uf2, off + BLOCK_SIZE - 4) != UF2_MAGIC_END:
            return False
        if _u32(uf2, off + 28) != UF2_FAMILY_ID:
            return False
    return True


def read_uf2(uf2: bytes) -> bytes | None:
    """Recover the concatenated payload bytes, or None for a bad/mismatched image."""
    if not validate_uf2(uf2):
        return None
    out = bytearray()
    for off in range(0, len(uf2), BLOCK_SIZE):
        out += uf2[off + 32 : off + 32 + PAYLOAD_SIZE]
    return bytes(out)


def _parse_blocks(uf2: bytes) -> list[tuple[int, bytes]]:
    return [
        (_u32(uf2, off + 12), uf2[off + 32 : off + 32 + PAYLOAD_SIZE])
        for off in range(0, len(uf2), BLOCK_SIZE)
    ]


def merge_uf2(images: list[bytes], fill_gaps: bool = False) -> bytes:
    """Merge UF2 images by flash address, renumbering blocks.

    With ``fill_gaps``, holes between the lowest and highest addresses become
    zero-filled blocks so the merged image maps the full range contiguously.
    """
    entries = [block for image in images for block in _parse_blocks(image)]
    entries.sort(key=lambda entry: entry[0])
    if fill_gaps:
        filled: list[tuple[int, bytes]] = []
        for address, payload in entries:
            if filled:
                gap = filled[-1][0] + PAYLOAD_SIZE
                while gap < address:
                    filled.append((gap, b"\x00" * PAYLOAD_SIZE))
                    gap += PAYLOAD_SIZE
            filled.append((address, payload))
        entries = filled
    num_blocks = len(entries)
    out = bytearray()
    for i, (address, payload) in enumerate(entries):
        out += _block(UF2_FAMILY_ID, address, payload, i, num_blocks)
    return bytes(out)
