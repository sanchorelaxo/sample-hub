"""Conversion matrix (SPEC.md §6): canonical bytes -> target bytes.

Each function is a pure transform of (canonical WAV bytes, params) with no
device I/O. ffmpeg is spawned per call, stdin -> stdout, so parallel jobs never
contend — the same no-singleton contract as the normalizer.

Target formats (verified against ffmpeg output and, for SP-404, SparkyMK2):

    Spotykach : 48 kHz stereo float32 WAV, plain tag 0x0003 (pass-through)
    SP-404    : 48 kHz 16-bit linear WAV (native device rate per SparkyMK2,
                which stores SMP samples at SAMPLE_RATE = 48_000)
    OP-1 drum : AIFC (compression "sowt") mono 44.1 kHz 16-bit, <= 12 s
    Wave Bard : mono 48 kHz float32 WAV, tuned to nearest C, silence trimmed

Pitch "to C" for Wave Bard uses ffmpeg's ``asetrate`` + ``aresample``, which
shifts pitch by resampling the sample rate (a tape-speed shift). It changes the
duration by the same factor — a known simplification. A duration-preserving
shift (rubberband) can replace it in a later milestone without changing the
matrix contract.
"""
from __future__ import annotations

import array
import io
import math
import shutil
import struct
import subprocess

from server.library import CANONICAL_CHANNELS, CANONICAL_RATE, FLOAT_FORMAT_TAG, NormalizeError

OP1_DRUM_MAX_SECONDS = 12.0
OP1_RATE = 44100
SP404_RATE = 48000  # SparkyMK2 crates/sp404-formats/src/smp.rs: SAMPLE_RATE = 48_000


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(args: list[str], stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        [_ffmpeg(), "-nostdin", "-v", "error", *args],
        input=stdin,
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()
        raise NormalizeError(detail[-400:] if detail else "ffmpeg failed")
    return proc.stdout


def _decode_float(src: bytes, channels: int, rate: int) -> bytes:
    """Decode any audio bytes to raw interleaved little-endian float32."""
    return _run(
        ["-i", "pipe:0", "-f", "f32le", "-acodec", "pcm_f32le",
         "-ar", str(rate), "-ac", str(channels), "-"],
        stdin=src,
    )


def _write_float_wav(pcm: bytes, channels: int, rate: int) -> bytes:
    """Wrap raw interleaved f32le PCM in a plain IEEE-float WAV (tag 0x0003)."""
    bits = 32
    block_align = channels * bits // 8
    byte_rate = rate * block_align
    data_len = len(pcm)
    riff_size = 4 + (8 + 16) + (8 + data_len)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", riff_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", FLOAT_FORMAT_TAG))
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", rate))
    buf.write(struct.pack("<I", byte_rate))
    buf.write(struct.pack("<H", block_align))
    buf.write(struct.pack("<H", bits))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm)
    return buf.getvalue()


def _as_floats(pcm: bytes) -> array.array:
    samples = array.array("f")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 4)])
    return samples


def _trim_silence(samples: array.array) -> array.array:
    """Drop leading/trailing samples below ~-60 dB of the peak."""
    if not samples:
        return samples
    peak = max(abs(x) for x in samples)
    if peak <= 1e-12:
        return array.array("f")
    threshold = peak * 1e-3
    start = 0
    end = len(samples)
    for i, x in enumerate(samples):
        if abs(x) >= threshold:
            start = i
            break
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) >= threshold:
            end = i + 1
            break
    return samples[start:end]


def _detect_f0(samples: array.array, rate: int) -> float:
    """Dominant frequency via zero crossings (clean, silence-free signal)."""
    crossings = 0
    for i in range(1, len(samples)):
        if (samples[i - 1] < 0) != (samples[i] < 0):
            crossings += 1
    seconds = len(samples) / rate
    return crossings / 2 / seconds if seconds else 0.0


def _nearest_c_factor(f0: float) -> float:
    """Ratio (target/f0) that moves f0 to the nearest C note."""
    midi = 69.0 + 12.0 * math.log2(f0 / 440.0)
    c_note = round(midi / 12.0) * 12  # MIDI numbers where note % 12 == 0 are C
    target = 440.0 * (2.0 ** ((c_note - 69.0) / 12.0))
    return target / f0


def _pitch_shift(pcm: bytes, factor: float, rate: int) -> bytes:
    """Shift pitch by ``factor`` (tape-speed resample), preserving sample rate."""
    new_rate = int(round(rate * factor))
    return _run(
        ["-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-af", f"asetrate={new_rate},aresample={rate}",
         "-f", "f32le", "-acodec", "pcm_f32le",
         "-ar", str(rate), "-ac", "1", "-"],
        stdin=pcm,
    )


# ---- audio rows --------------------------------------------------------

def to_spotykach(src: bytes) -> bytes:
    """Canonical 48k stereo float32 WAV -> itself (re-encoded, tag 0x0003)."""
    pcm = _decode_float(src, CANONICAL_CHANNELS, CANONICAL_RATE)
    return _write_float_wav(pcm, CANONICAL_CHANNELS, CANONICAL_RATE)


def to_sp404(src: bytes) -> bytes:
    """Canonical -> 16-bit linear 48 kHz stereo WAV (SparkyMK2 native rate)."""
    return _run(
        ["-i", "pipe:0", "-ar", str(SP404_RATE), "-ac", "2",
         "-c:a", "pcm_s16le", "-f", "wav", "-"],
        stdin=src,
    )


def to_op1_drum(src: bytes, filename: str) -> bytes:
    """Canonical -> AIFC (sowt) mono 44.1 kHz 16-bit, refusing > 12 s."""
    mono = _decode_float(src, 1, OP1_RATE)  # measure duration from the mono decode
    duration = len(mono) / 4 / OP1_RATE
    if duration > OP1_DRUM_MAX_SECONDS:
        raise ValueError(
            f"{filename}: {duration:.1f}s exceeds the {OP1_DRUM_MAX_SECONDS:.0f}s drum cap"
        )
    return _run(
        ["-i", "pipe:0", "-ar", str(OP1_RATE), "-ac", "1",
         "-c:a", "pcm_s16le", "-f", "aiff", "-"],
        stdin=src,
    )


def to_wave_bard(src: bytes) -> bytes:
    """Canonical -> mono 48 kHz float32 WAV, tuned to nearest C, silence trimmed."""
    mono = _decode_float(src, 1, CANONICAL_RATE)
    samples = _trim_silence(_as_floats(mono))
    f0 = _detect_f0(samples, CANONICAL_RATE)
    factor = _nearest_c_factor(f0) if f0 > 0.0 else 1.0
    pcm = _pitch_shift(samples.tobytes(), factor, CANONICAL_RATE) if abs(factor - 1.0) > 1e-6 \
        else samples.tobytes()
    return _write_float_wav(pcm, 1, CANONICAL_RATE)


# ---- rhythm rows -------------------------------------------------------

def _gate_pattern(step_data: dict) -> tuple[int, list[int]]:
    steps = step_data.get("steps", 16)
    gates = step_data.get("gates", [])
    return steps, [i for i, g in enumerate(gates) if g]


def wave_bard_rhythm(step_data: dict) -> dict:
    """Step/gate JSON -> Wave Bard rhythm bank entry."""
    steps, pattern = _gate_pattern(step_data)
    return {"steps": steps, "pattern": pattern, "type": "rhythm"}


def fx_wizard_rhythm(step_data: dict) -> dict:
    """Step/gate JSON -> FX Wizard Chamber rhythm/step format."""
    steps, pattern = _gate_pattern(step_data)
    return {"step_count": steps, "pattern": pattern}
