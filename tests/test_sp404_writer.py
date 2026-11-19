"""Milestone 5 — SP-404 writer: 16-bit 48 kHz WAV -> SD ``IMPORT/``.

The MK2 stages audio into the SD card's ``IMPORT/`` folder; its own IMPORT
browser (SHIFT+pad13) finds them there. Device-free tests decode the real bytes
``stage_import`` writes (16-bit 48 kHz stereo WAV, no mocks) against a fake card
root. The real USB transfer is SparkyMK2's SP404REMOTE protocol and is deferred;
a card-root write is opt-in via the ``SP404_CARD`` env var behind
``--real-devices``.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

from server.devices.sp404 import stage_import

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
    """A 48 kHz stereo float32 WAV — the hub's canonical form."""
    return run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ar", "48000", "-ac", "2",
            "-c:a", "pcm_f32le", "-f", "wav", "-",
        ]
    )


def wav_facts(data: bytes) -> tuple[int, int, int, int]:
    """Decode the WAV fmt chunk -> (codec, channels, rate, bits)."""
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


# ---- device-free --------------------------------------------------------

def test_stage_import_writes_16bit_48k_stereo_wav(tmp_path):
    report = stage_import(canonical_wav(), "kick.wav", card_root=tmp_path)
    assert report["rel"] == "IMPORT/kick.wav"
    written = (tmp_path / "IMPORT" / "kick.wav").read_bytes()
    assert wav_facts(written) == (1, 2, 48000, 16)


def test_stage_import_refuses_overwrite(tmp_path):
    stage_import(canonical_wav(), "kick.wav", card_root=tmp_path)
    second = stage_import(canonical_wav(), "kick.wav", card_root=tmp_path)
    assert second["rel"] == "IMPORT/kick-2.wav"
    assert (tmp_path / "IMPORT" / "kick.wav").exists()
    assert (tmp_path / "IMPORT" / "kick-2.wav").exists()


def test_stage_import_force_overwrites(tmp_path):
    stage_import(canonical_wav(), "kick.wav", card_root=tmp_path)
    second = stage_import(canonical_wav(freq=880.0), "kick.wav", card_root=tmp_path, force=True)
    assert second["rel"] == "IMPORT/kick.wav"
    assert sorted(p.name for p in (tmp_path / "IMPORT").iterdir()) == ["kick.wav"]


def test_stage_import_sanitizes_unsafe_name(tmp_path):
    report = stage_import(canonical_wav(), "../kick|bad.wav", card_root=tmp_path)
    assert report["rel"] == "IMPORT/kick_bad.wav"
    assert (tmp_path / "IMPORT" / "kick_bad.wav").exists()


# ---- real device (gated, opt-in) ----------------------------------------

@pytest.mark.real_devices
def test_sp404_card_stage():
    card = os.environ.get("SP404_CARD")
    if not card:
        pytest.skip("set SP404_CARD to a mounted SD card to run this")
    assert card is not None
    report = stage_import(canonical_wav(), "hub-test.wav", card_root=Path(card))
    assert (Path(card) / "IMPORT" / "hub-test.wav").exists()
