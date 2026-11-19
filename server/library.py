"""Canonical normalizer: any supported WAV -> 48 kHz stereo float32 WAV.

The hub normalizes every incoming audio sample once, to the canonical working
form, before any destination transform runs (SPEC.md, "The Conversion Matrix").
The canonical form matches the ``wav_builder`` output that spottytube-ext
already produces for the Spotykach: 48 kHz, stereo, 32-bit IEEE float, written
with the plain IEEE-float format tag (``0x0003``) rather than
``WAVE_FORMAT_EXTENSIBLE`` (``0xFFFE``), because the Spotykach firmware
(libDaisy) only recognizes the plain float tag.

Mono upmix note: a mono source is upmixed to stereo by ffmpeg, which applies a
-3dB pan-law attenuation per channel so the total power stays constant rather
than doubling. A mono sample is therefore ~0.707x its original amplitude on
each channel after normalization; this is intentional and mirrors how every
mono->stereo path in the hub behaves.

Concurrency contract: ``normalize`` is stateless and re-entrant. ffmpeg is NOT
a singleton here -- every call spawns its own subprocess and holds no shared
lock, temp file, or module-level state, so parallel jobs (one per instrument)
never contend. The decode runs straight to raw float32 on stdout and the bytes
are captured in memory, so there is no shared temp path for concurrent calls to
clobber.

Milestone 1 scope is WAV input. ffmpeg decodes regardless of container, so the
same path will accept the other source formats (AIFF, MP3, OGG, ...) when the
library cache grows to feed them in; that acceptance is asserted in a later
milestone.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

CANONICAL_RATE = 48000
CANONICAL_CHANNELS = 2
FLOAT_FORMAT_TAG = 0x0003  # IEEE float; libDaisy/Spotykach read this tag


class NormalizeError(RuntimeError):
    """Raised when ffmpeg cannot decode the source into the canonical form."""


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def normalize(src: Path | str, dst: Path | str) -> Path:
    """Normalize ``src`` to the canonical 48 kHz stereo float32 WAV at ``dst``.

    Decodes the source with a fresh ffmpeg subprocess straight to raw
    interleaved little-endian float32 on stdout, then writes the canonical WAV
    header around the captured bytes. No shared temp files and no global lock:
    concurrent calls are fully independent.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-i", str(src),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ar", str(CANONICAL_RATE),
        "-ac", str(CANONICAL_CHANNELS),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise NormalizeError(f"ffmpeg failed for {src.name}: {detail[-400:]}")
    _write_float_wav(dst, result.stdout)
    return dst


def _write_float_wav(path: Path, pcm: bytes) -> None:
    """Write a canonical IEEE-float WAV (format tag 0x0003, 16-byte fmt)."""
    bits = 32
    block_align = CANONICAL_CHANNELS * bits // 8
    byte_rate = CANONICAL_RATE * block_align
    fmt_len = 16
    data_len = len(pcm)
    riff_size = 4 + (8 + fmt_len) + (8 + data_len)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", riff_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", fmt_len))
        f.write(struct.pack("<H", FLOAT_FORMAT_TAG))
        f.write(struct.pack("<H", CANONICAL_CHANNELS))
        f.write(struct.pack("<I", CANONICAL_RATE))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits))
        f.write(b"data")
        f.write(struct.pack("<I", data_len))
        f.write(pcm)
