"""Milestone 4 — OP-1 drum writer, device-free + gated real-device.

A usable OP-1 kit is a full AIFC: ``FORM``/``AIFF`` with ``FVER`` + ``COMM`` +
``APPL`` + ``SSND`` chunks, mono 44.1 kHz 16-bit, <= 12 s. Device-free tests
decode the real bytes ``build_drum_kit`` produces (no mocks) and drive the real
``device.write_drum_kit`` against a fake OP-1 volume. Mount detection is gated
behind ``--real-devices``.
"""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest

from server.devices.op1 import MAX_SECONDS, build_drum_kit, write_drum_kit

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


def aiff_chunks(data: bytes) -> dict[bytes, bytes]:
    """Big-endian AIFF chunk table (cid -> payload)."""
    assert data[:4] == b"FORM" and data[8:12] == b"AIFF", "not an AIFF"
    chunks: dict[bytes, bytes] = {}
    off = 12
    while off + 8 <= len(data):
        cid = data[off : off + 4]
        size = struct.unpack(">I", data[off + 4 : off + 8])[0]
        chunks[cid] = data[off + 8 : off + 8 + size]
        off += 8 + size + (size & 1)
    return chunks


def op1_layout(root: Path) -> Path:
    """A volume with the four folders the OP-1 exposes in disk mode."""
    for d in ("synth", "drum", "tape", "album"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


# ---- device-free --------------------------------------------------------

def test_build_drum_kit_is_full_aifc():
    payload, name = build_drum_kit(canonical_wav(), "mysample.wav")
    assert name == "mysample"
    chunks = aiff_chunks(payload)
    assert set(chunks) >= {b"FVER", b"COMM", b"APPL", b"SSND"}
    # COMM: mono, 16-bit, little-endian ("sowt")
    assert struct.unpack(">h", chunks[b"COMM"][:2])[0] == 1
    assert struct.unpack(">h", chunks[b"COMM"][6:8])[0] == 16
    assert chunks[b"COMM"][18:22] == b"sowt"
    # APPL carries the op-1 slice metadata and the kit name
    assert chunks[b"APPL"][:4] == b"op-1"
    assert b"mysample" in chunks[b"APPL"]


def test_drum_kit_enforces_12_second_cap():
    with pytest.raises(ValueError, match="12 seconds"):
        build_drum_kit(canonical_wav(seconds=MAX_SECONDS + 1.0), "toolong.wav")


def test_write_drum_kit_lands_in_drum_user(tmp_path):
    mount = op1_layout(tmp_path)
    report = write_drum_kit(canonical_wav(), "mysample.wav", mount=mount)
    assert report["rel"] == "drum/user/mysample.aif"
    written = (mount / "drum" / "user" / "mysample.aif").read_bytes()
    assert written[:4] == b"FORM" and written[8:12] == b"AIFF"


def test_write_drum_kit_never_overwrites(tmp_path):
    mount = op1_layout(tmp_path)
    write_drum_kit(canonical_wav(), "mysample.wav", mount=mount)
    second = write_drum_kit(canonical_wav(), "mysample.wav", mount=mount)
    assert second["rel"] == "drum/user/mysample-2.aif"
    assert (mount / "drum" / "user" / "mysample.aif").exists()
    assert (mount / "drum" / "user" / "mysample-2.aif").exists()


# ---- real device (gated) ------------------------------------------------

@pytest.mark.real_devices
def test_op1_mount_present():
    from server.devices.op1 import find_mount
    mount = find_mount()
    if mount is None:
        pytest.skip("OP-1 not mounted in disk mode")
    assert (mount / "drum").is_dir()
