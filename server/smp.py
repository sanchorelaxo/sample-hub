"""SP-404MK2 ``.SMP`` reader — decode the proprietary sample container.

Port of ``TuscanPL/SparkyMK2`` ``crates/sp404-formats/src/smp.rs``, the
authoritative MK2 reference (already cited in ``server/convert.py`` for the
48 kHz rate). The container is a 512-byte header followed by interleaved
big-endian 16-bit PCM:

    0x00..0x04  magic ``RFWV``
    0x04..0x08  u32 BE  size field = PCM byte count + 504
    0x08..0x0c  u32 BE  sample rate (must be 48000)
    0x0c..0x10  u32 BE  channel count (1 or 2)
    0x10..0x14  u32 BE  bit depth (must be 16)
    0x14..0x20  reserved (zeros)
    0x20..0x30  MD5 digest over bytes 0x04..0x20 (the four fields)
    0x30..0x200 filler (no meaning, ignored)
    0x200..     interleaved big-endian 16-bit PCM

``decode_smp`` verifies length, magic, format fields, digest and the declared
PCM size, then returns ``(channels, pcm)`` with ``pcm`` byte-swapped to host
little-endian. ``smp_to_wav`` wraps that PCM in a plain 16-bit 48 kHz WAV so
the canonical normalizer can consume it like any other source.
"""

from __future__ import annotations

import array
import hashlib
import struct

SMP_RATE = 48000
SMP_BITS = 16
SMP_MAGIC = b"RFWV"

_HEADER_LEN = 512
_SIZE_BIAS = 504  # header minus magic (4) and the size field (4)
_DIGEST_OFFSET = 0x20
_DIGEST_LEN = 16
_FIELDS = slice(4, 20)


class SmpError(ValueError):
    """Raised when a ``.SMP`` file is malformed or unsupported."""


def decode_smp(data: bytes) -> tuple[int, bytes]:
    """Decode a MK2 ``.SMP`` file to ``(channels, little-endian 16-bit PCM)``."""
    if len(data) < _HEADER_LEN:
        raise SmpError("SMP file too short")
    if data[0:4] != SMP_MAGIC:
        raise SmpError(f"not a recognized SMP file (magic={data[0:4]!r})")
    size_field = struct.unpack(">I", data[4:8])[0]
    rate = struct.unpack(">I", data[8:12])[0]
    channels = struct.unpack(">I", data[12:16])[0]
    bits = struct.unpack(">I", data[16:20])[0]
    if rate != SMP_RATE:
        raise SmpError(f"unsupported SMP sample rate {rate}")
    if bits != SMP_BITS:
        raise SmpError(f"unsupported SMP bit depth {bits}")
    if channels not in (1, 2):
        raise SmpError(f"unsupported SMP channel count {channels}")
    if hashlib.md5(data[_FIELDS]).digest() != data[_DIGEST_OFFSET:_DIGEST_OFFSET + _DIGEST_LEN]:
        raise SmpError("SMP header digest mismatch")
    pcm = data[_HEADER_LEN:]
    declared = size_field - _SIZE_BIAS
    if len(pcm) != declared:
        raise SmpError(f"SMP size mismatch (declared {declared}, got {len(pcm)})")
    samples = array.array("h")
    samples.frombytes(pcm)
    samples.byteswap()  # big-endian on disk -> host little-endian
    return channels, samples.tobytes()


def smp_to_wav(data: bytes) -> bytes:
    """Decode a MK2 ``.SMP`` file to a plain 16-bit 48 kHz WAV in memory."""
    channels, pcm = decode_smp(data)
    block_align = channels * SMP_BITS // 8
    byte_rate = SMP_RATE * block_align
    data_len = len(pcm)
    riff_size = 4 + (8 + 16) + (8 + data_len)
    header = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)  # PCM
        + struct.pack("<H", channels)
        + struct.pack("<I", SMP_RATE)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", block_align)
        + struct.pack("<H", SMP_BITS)
        + b"data"
        + struct.pack("<I", data_len)
    )
    return header + pcm
