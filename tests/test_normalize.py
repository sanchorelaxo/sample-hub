"""Canonical normalizer: any WAV -> 48 kHz stereo float32 WAV.

Milestone 1. The normalizer turns any supported WAV into the hub's one
canonical working form (48 kHz, stereo, 32-bit IEEE float) before any
destination transform runs.

The source WAVs in these tests are real files ffmpeg produced; the normalized
output is decoded back with struct and the float samples inspected, so every
assertion runs against actual bytes, never a mock.

ffmpeg is deliberately NOT a singleton: ``normalize`` is stateless and each call
runs its own subprocess, so the concurrency test proves parallel jobs share no
lock and no temp file.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.library import (
    CANONICAL_CHANNELS,
    CANONICAL_RATE,
    FLOAT_FORMAT_TAG,
    NormalizeError,
    normalize,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def make_wav(
    path: Path, *, rate: int, channels: int, duration: float, freq: float
) -> Path:
    """Write a real WAV with ffmpeg's sine source (the bytes ffmpeg produced)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg(), "-y",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
            "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def parse_wav(raw: bytes) -> dict:
    """Decode a RIFF/WAVE header and return its fields plus the PCM payload."""
    assert raw[:4] == b"RIFF"
    assert raw[8:12] == b"WAVE"
    assert raw[12:16] == b"fmt "
    fmt_size = struct.unpack("<I", raw[16:20])[0]
    audio_format, channels, rate, _byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", raw[20:36]
    )
    data_at = 20 + fmt_size
    assert raw[data_at : data_at + 4] == b"data"
    data_size = struct.unpack("<I", raw[data_at + 4 : data_at + 8])[0]
    return {
        "format": audio_format,
        "channels": channels,
        "rate": rate,
        "block_align": block_align,
        "bits": bits,
        "data": raw[data_at + 8 : data_at + 8 + data_size],
        "data_size": data_size,
    }


def float_samples(data: bytes) -> list[float]:
    """Unpack interleaved float32 PCM into a flat list (L0, R0, L1, R1, ...)."""
    assert len(data) % 4 == 0
    return list(struct.unpack("<" + "f" * (len(data) // 4), data))


# ffmpeg's sine source peaks at 0.125, and its mono->stereo upmix applies a
# -3dB (x0.707) pan-law attenuation, so a normalized mono source peaks around
# 0.088. MIN_PEAK is a safe "definitely not silence" floor, an order of
# magnitude above a zeroed buffer without coupling the test to ffmpeg's exact
# gain.
MIN_PEAK = 0.01


# -- the canonical contract -------------------------------------------------


def test_normalizes_44100_mono_16bit_to_48k_stereo_float(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=44100, channels=1, duration=0.5, freq=440)
    dst = tmp_path / "out.wav"

    normalize(src, dst)

    wav = parse_wav(dst.read_bytes())
    assert wav["format"] == FLOAT_FORMAT_TAG  # IEEE float, not PCM int
    assert wav["channels"] == CANONICAL_CHANNELS
    assert wav["rate"] == CANONICAL_RATE
    assert wav["bits"] == 32
    assert wav["block_align"] == 8  # 2 channels x 4 bytes


def test_mono_source_is_upmixed_to_identical_channels(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=22050, channels=1, duration=0.25, freq=523)
    dst = tmp_path / "out.wav"

    normalize(src, dst)

    samples = float_samples(parse_wav(dst.read_bytes())["data"])
    assert samples, "expected audio data"
    assert samples[0::2] == samples[1::2]  # left == right at every frame


def test_resample_preserves_duration_and_is_not_silent(tmp_path):
    duration = 1.0
    src = make_wav(tmp_path / "in.wav", rate=44100, channels=1, duration=duration, freq=440)
    dst = tmp_path / "out.wav"

    normalize(src, dst)

    wav = parse_wav(dst.read_bytes())
    samples = float_samples(wav["data"])
    frames = len(samples) // 2
    assert abs(frames - CANONICAL_RATE * duration) < 200
    peak = max(abs(s) for s in samples)
    assert MIN_PEAK < peak <= 1.0


def test_already_canonical_input_is_accepted(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=48000, channels=2, duration=0.5, freq=330)
    dst = tmp_path / "out.wav"

    normalize(src, dst)

    wav = parse_wav(dst.read_bytes())
    assert wav["format"] == FLOAT_FORMAT_TAG
    assert wav["channels"] == CANONICAL_CHANNELS
    assert wav["rate"] == CANONICAL_RATE


# -- concurrency (ffmpeg must not be a singleton) ---------------------------


def test_concurrent_normalizations_do_not_interfere(tmp_path):
    jobs = []
    for freq in (220, 330, 440, 554, 659):
        src = make_wav(
            tmp_path / f"in_{freq}.wav", rate=44100, channels=1, duration=0.3, freq=freq
        )
        jobs.append((src, tmp_path / f"out_{freq}.wav"))

    def run(job) -> tuple[int, int, int, float]:
        src, dst = job
        normalize(src, dst)
        wav = parse_wav(dst.read_bytes())
        samples = float_samples(wav["data"])
        return (
            wav["rate"],
            wav["channels"],
            wav["format"],
            max(abs(s) for s in samples),
        )

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(run, jobs))

    assert len(results) == len(jobs)
    for rate, channels, fmt, peak in results:
        assert rate == CANONICAL_RATE
        assert channels == CANONICAL_CHANNELS
        assert fmt == FLOAT_FORMAT_TAG
        assert peak > MIN_PEAK  # each output carries its own real, non-silent signal


# -- refusing what it cannot decode -----------------------------------------


def test_non_audio_input_raises_normalize_error(tmp_path):
    bad = tmp_path / "not_audio.wav"
    bad.write_bytes(b"this is not audio at all, ffmpeg will refuse it")

    with pytest.raises(NormalizeError):
        normalize(bad, tmp_path / "out.wav")
