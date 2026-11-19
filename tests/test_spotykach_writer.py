"""Milestone 3 — Spotykach writer, device-free + gated real-device.

The matrix's Spotykach output (48 kHz stereo float32 WAV) must be exactly what
``sk_usb.DEVICE_FORMAT`` accepts, and the writer must place it in a slot via the
existing transport. Device-free tests drive the real ``CardTransport`` (plain
``SK/<tape>/<slot>.WAV`` files) — no mocks. The USB path is exercised behind
``--real-devices``.
"""
from __future__ import annotations

import subprocess

import pytest

from server.convert import to_spotykach
from server.devices.spotykach import (
    SLOTS_PER_TAPE,
    TAPE_ORDER,
    SkLink,
    find_midi_port,
    find_serial_port,
    validate,
    write_sample,
)

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


def noncanonical_wav() -> bytes:
    """44.1 kHz mono 16-bit WAV — the wrong shape for Spotykach."""
    return run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-ar", "44100", "-ac", "1",
            "-c:a", "pcm_s16le", "-f", "wav", "-",
        ]
    )


# ---- device-free --------------------------------------------------------

def test_matrix_output_is_uploadable():
    """The conversion matrix's Spotykach row passes the device's own check."""
    out = to_spotykach(canonical_wav())
    assert validate(out) == []


def test_write_sample_via_card_transport(tmp_path):
    """Real CardTransport writes the exact bytes to SK/<tape>/<slot>.WAV."""
    (tmp_path / "SK").mkdir()
    data = to_spotykach(canonical_wav())
    written = write_sample(data, "B", 1, preferred="card", card_root=str(tmp_path))
    slot_file = tmp_path / "SK" / "B" / "1.WAV"
    assert slot_file.read_bytes() == data
    assert written == len(data)


def test_write_sample_rejects_wrong_format(tmp_path):
    """Non-canonical bytes are refused before any transport opens."""
    (tmp_path / "SK").mkdir()
    with pytest.raises(ValueError, match="sample_rate"):
        write_sample(noncanonical_wav(), "B", 1, preferred="card", card_root=str(tmp_path))
    assert not (tmp_path / "SK" / "B").exists()


def test_write_sample_rejects_bad_tape_or_slot(tmp_path):
    (tmp_path / "SK").mkdir()
    data = to_spotykach(canonical_wav())
    with pytest.raises(ValueError, match="tape"):
        write_sample(data, "Z", 1, preferred="card", card_root=str(tmp_path))
    with pytest.raises(ValueError, match="slot"):
        write_sample(data, "B", SLOTS_PER_TAPE + 1, preferred="card", card_root=str(tmp_path))
    assert TAPE_ORDER == "BGPRTY"


# ---- real device (gated) ------------------------------------------------

@pytest.mark.real_devices
def test_spotykach_presence_and_handshake():
    """Read-only: confirm the unit is present, and handshake if already in
    transfer mode. Never switches modes or writes a slot, so it is safe."""
    port = find_serial_port()
    if port is None:
        assert find_midi_port() is not None, "no Spotykach in either mode"
        pytest.skip("Spotykach present in MIDI mode; serial handshake needs transfer mode")
    link = SkLink(port)
    try:
        info = link.hello()
        assert info.proto_major == 1
        assert info.slots >= 1
    finally:
        link.close()
