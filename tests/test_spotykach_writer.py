"""Milestone 3 — Spotykach writer, device-free + gated real-device.

The matrix's Spotykach output (48 kHz stereo float32 WAV) must be exactly what
``sk_usb.DEVICE_FORMAT`` accepts, and the writer must place it in the first
empty slot of the ``B G P R T Y`` chain using *append* mode — nothing shifts.
Device-free tests drive the real ``CardTransport`` (plain
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


def test_write_sample_appends_to_first_free_slot(tmp_path):
    """An empty card takes the sample at B/1 via append (nothing shifts)."""
    (tmp_path / "SK").mkdir()
    data = to_spotykach(canonical_wav())
    placement = write_sample(data, "B", preferred="card", card_root=str(tmp_path))
    assert placement == {
        "transport": "card",
        "tape": "B",
        "slot": 1,
        "shifted": 0,
        "archived": None,
        "detail": f"{len(data):,} bytes verified",
    }
    assert (tmp_path / "SK" / "B" / "1.WAV").read_bytes() == data


def test_write_sample_appends_past_occupied_slot(tmp_path):
    """Append lands on the first empty slot and moves nothing."""
    (tmp_path / "SK").mkdir()
    first = to_spotykach(canonical_wav(freq=440.0))
    second = to_spotykach(canonical_wav(freq=660.0))
    p1 = write_sample(first, "B", preferred="card", card_root=str(tmp_path))
    p2 = write_sample(second, "B", preferred="card", card_root=str(tmp_path))
    assert (p1["tape"], p1["slot"]) == ("B", 1)
    assert (p2["tape"], p2["slot"]) == ("B", 2)
    assert p2["shifted"] == 0
    assert p2["archived"] is None
    # The first sample was not moved or overwritten.
    assert (tmp_path / "SK" / "B" / "1.WAV").read_bytes() == first
    assert (tmp_path / "SK" / "B" / "2.WAV").read_bytes() == second


def test_write_sample_rejects_wrong_format(tmp_path):
    """Non-canonical bytes are refused before any transport opens."""
    (tmp_path / "SK").mkdir()
    with pytest.raises(ValueError, match="sample_rate"):
        write_sample(noncanonical_wav(), "B", preferred="card", card_root=str(tmp_path))
    assert not (tmp_path / "SK" / "B").exists()


def test_write_sample_rejects_bad_tape(tmp_path):
    (tmp_path / "SK").mkdir()
    data = to_spotykach(canonical_wav())
    with pytest.raises(ValueError, match="tape"):
        write_sample(data, "Z", preferred="card", card_root=str(tmp_path))
    assert TAPE_ORDER == "BGPRTY"


# ---- real device (gated) ------------------------------------------------

@pytest.mark.real_devices
def test_spotykach_appends_a_sample():
    """Place a real sample on the unit via append mode (first empty slot).

    Append never moves or overwrites an occupied slot, so this is safe on a
    live unit; it only fills one empty slot and reads it back to verify.
    """
    if find_serial_port() is None and find_midi_port() is None:
        pytest.skip("no Spotykach present")
    data = to_spotykach(canonical_wav(seconds=0.5))
    placement = write_sample(data, "B", preferred="usb")
    assert placement["transport"] == "usb"
    assert placement["tape"] in TAPE_ORDER
    assert 1 <= placement["slot"] <= SLOTS_PER_TAPE
    assert placement["shifted"] == 0
    assert placement["archived"] is None
