"""SP-404MK2 ``.SMP`` decoder (``server/smp.py``) — pure, device-free tests.

Fixtures are real bytes built to the SparkyMK2 ``smp.rs`` RFWV layout (the
authoritative MK2 reference, already cited in ``server/convert.py``): a
512-byte header (magic ``RFWV``, four big-endian fields, an MD5 digest over
those fields, non-zero filler) followed by interleaved big-endian 16-bit PCM at
48 kHz. No ffmpeg and no mocks.
"""

from __future__ import annotations

import hashlib
import struct

import pytest

from server.smp import SMP_RATE, SmpError, decode_smp, smp_to_wav


def make_smp(channels: int, samples: list[int]) -> bytes:
    """Build a valid SMP file (SparkyMK2 ``smp.rs`` header layout)."""
    header = bytearray(512)
    header[0:4] = b"RFWV"
    header[4:8] = struct.pack(">I", len(samples) * 2 + 504)
    header[8:12] = struct.pack(">I", 48000)
    header[12:16] = struct.pack(">I", channels)
    header[16:20] = struct.pack(">I", 16)
    header[0x20:0x30] = hashlib.md5(header[4:20]).digest()
    header[0x30:512] = b"\xa5" * (512 - 0x30)  # filler the decoder must skip
    pcm = b"".join(struct.pack(">h", s) for s in samples)
    return bytes(header) + pcm


def test_decode_smp_stereo_swaps_big_endian():
    samples = [0, 1000, -1000, 32767, -32768]
    channels, pcm = decode_smp(make_smp(2, samples))
    assert channels == 2
    # container stores big-endian; the decoder returns little-endian
    assert pcm == b"".join(struct.pack("<h", s) for s in samples)


def test_decode_smp_mono():
    samples = [1, -2, 3]
    channels, pcm = decode_smp(make_smp(1, samples))
    assert channels == 1
    assert pcm == b"".join(struct.pack("<h", s) for s in samples)


def test_smp_to_wav_is_16bit_48k():
    samples = [0, 1000, -1000]
    wav = smp_to_wav(make_smp(2, samples))
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    # fmt chunk: PCM tag 1, 2 ch, 48000 Hz, 16-bit
    assert struct.unpack("<H", wav[20:22])[0] == 1
    assert struct.unpack("<H", wav[22:24])[0] == 2
    assert struct.unpack("<I", wav[24:28])[0] == SMP_RATE
    assert struct.unpack("<H", wav[34:36])[0] == 16
    # data chunk carries the byte-swapped PCM
    assert wav[44:] == b"".join(struct.pack("<h", s) for s in samples)


def test_decode_rejects_bad_magic():
    data = bytearray(make_smp(1, [0]))
    data[0] = ord("X")
    with pytest.raises(SmpError):
        decode_smp(bytes(data))


def test_decode_rejects_unsupported_rate():
    data = bytearray(make_smp(1, [0]))
    data[8:12] = struct.pack(">I", 44100)
    data[0x20:0x30] = hashlib.md5(data[4:20]).digest()  # fix digest so rate check fires
    with pytest.raises(SmpError):
        decode_smp(bytes(data))


def test_decode_rejects_digest_mismatch():
    data = bytearray(make_smp(1, [0]))
    data[0x21] ^= 1
    with pytest.raises(SmpError):
        decode_smp(bytes(data))


def test_decode_rejects_size_mismatch():
    data = make_smp(1, [0, 0])
    with pytest.raises(SmpError):
        decode_smp(data[:-1])  # drop one PCM byte


def test_decode_rejects_too_short():
    with pytest.raises(SmpError):
        decode_smp(b"RFWV")
