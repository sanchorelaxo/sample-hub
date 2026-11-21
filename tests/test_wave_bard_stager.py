"""Milestone 6 — Wave Bard stager: bank draft (samples + rhythms + scales).

The draft emits a "ready to flash" manifest, not the .uf2 (that is the Bastl
loader's job). Device-free tests feed generated canonical WAVs and assert the
staged samples are mono float32 WAVs at the selected quality rate and the
manifest round-trips as JSON.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import pytest

from server.devices.wave_bard import READY_TO_FLASH, build_draft
from server.pattern import pattern_to_steps

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


def make_state(
    banks: int = 1,
    samples_per_bank: int = 3,
    scales: int = 3,
    rhythms: int = 3,
) -> dict:
    state_banks = [
        {
            "id": f"b{i}",
            "name": f"bank {i}",
            "samples": [
                {"id": f"b{i}s{j}", "name": f"kick{i}{j}", "data": canonical_wav()}
                for j in range(samples_per_bank)
            ],
        }
        for i in range(banks)
    ]
    state_scales = [
        {"id": f"sc{i}", "name": f"scale {i}", "semitones": 1 << i}
        for i in range(scales)
    ]
    state_rhythms = [
        {"id": f"r{i}", "name": f"rhythm {i}", "steps": pattern_to_steps([i % 16], 16)}
        for i in range(rhythms)
    ]
    return {
        "sampleRate": 44100,
        "audioProcessing": {"fadeIn": True, "fadeOut": False, "trimQuiet": False, "maximize": False},
        "sequencerLength": 16,
        "banks": state_banks,
        "scales": state_scales,
        "rhythms": state_rhythms,
    }


def test_build_draft_stages_samples_and_manifest(tmp_path):
    draft = build_draft(make_state(), tmp_path)
    assert draft["state"] == READY_TO_FLASH
    assert draft["target"] == "wave-bard"
    assert draft["format"] == "wave-bard-loader"
    assert draft["sampleRate"] == 44100
    assert draft["audioProcessing"]["fadeIn"] is True
    assert draft["sequencerLength"] == 16
    assert len(draft["banks"]) == 1
    assert len(draft["banks"][0]["samples"]) == 3
    assert len(draft["scales"]) == 3
    assert len(draft["rhythms"]) == 3
    leaf = draft["banks"][0]["samples"][0]["file"]
    # staged sample is mono float32 WAV at the selected quality rate
    assert wav_facts((tmp_path / leaf).read_bytes()) == (3, 1, 44100, 32)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["state"] == READY_TO_FLASH
    assert manifest["target"] == "wave-bard"


def test_build_draft_honors_telephone_rate(tmp_path):
    state = make_state()
    state["sampleRate"] = 11025
    draft = build_draft(state, tmp_path)
    leaf = draft["banks"][0]["samples"][0]["file"]
    assert wav_facts((tmp_path / leaf).read_bytes()) == (3, 1, 11025, 32)


def test_build_draft_sanitizes_sample_name(tmp_path):
    state = make_state()
    state["banks"][0]["samples"][0]["name"] = "../kick|bad.wav"
    draft = build_draft(state, tmp_path)
    leaf = draft["banks"][0]["samples"][0]["file"]
    assert leaf.endswith(".wav")
    assert ".." not in leaf and "|" not in leaf and "/" not in leaf
    assert (tmp_path / leaf).exists()


def test_build_draft_rejects_uniformity_violation(tmp_path):
    state = make_state(banks=2)
    state["banks"][1]["samples"].append({"name": "extra", "data": canonical_wav()})
    with pytest.raises(ValueError):
        build_draft(state, tmp_path)
