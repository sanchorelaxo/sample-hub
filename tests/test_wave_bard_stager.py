"""Milestone 6 — Wave Bard stager: bank draft (samples + rhythms + scales).

v1 emits a "ready to flash" draft, not the .uf2 (that is the Bastl loader's
job). Device-free tests feed generated canonical WAVs and assert the staged
samples are mono 48 kHz float32 WAVs and the manifest round-trips as JSON.
"""
from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

from server.devices.wave_bard import READY_TO_FLASH, build_draft

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


def wav_facts(data: bytes) -> tuple[int, int, int, int]:
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    off = 12
    fmt = None
    while off + 8 <= len(data):
        cid = data[off : off + 4]
        size = struct.unpack("<I", data[off + 4 : off + 8])[0]
        if cid == b"fmt ":
            fmt = data[off + 8 : off + 8 + size]
        off += 8 + size + (size & 1)
    assert fmt is not None, "no fmt chunk"
    codec, channels, rate = struct.unpack("<HHI", fmt[:8])
    bits = struct.unpack("<H", fmt[14:16])[0]
    return codec, channels, rate, bits


def test_build_draft_stages_samples_and_manifest(tmp_path):
    draft = build_draft(
        "bank-a",
        [{"name": "kick", "data": canonical_wav()}],
        [{"steps": 16, "gates": [1, 0, 0, 0, 1, 0, 0, 0]}],
        [{"name": "minor"}],
        tmp_path,
    )
    assert draft["state"] == READY_TO_FLASH
    assert draft["bank"] == "bank-a"
    assert draft["samples"] == [{"name": "kick", "file": "kick.wav"}]
    assert draft["rhythms"] == [{"steps": 16, "pattern": [0, 4], "type": "rhythm"}]
    assert draft["scales"] == [{"name": "minor"}]
    # staged sample is mono 48 kHz float32 WAV
    assert wav_facts((tmp_path / "kick.wav").read_bytes()) == (3, 1, 48000, 32)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["state"] == READY_TO_FLASH
    assert manifest["bank"] == "bank-a"


def test_build_draft_sanitizes_sample_name(tmp_path):
    draft = build_draft(
        "b",
        [{"name": "../kick|bad.wav", "data": canonical_wav()}],
        [], [], tmp_path,
    )
    leaf = draft["samples"][0]["file"]
    assert leaf.endswith(".wav")
    assert ".." not in leaf and "|" not in leaf and "/" not in leaf
    assert (tmp_path / leaf).exists()
