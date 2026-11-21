"""Device source read-back (TODO_1.txt item 2)."""

from __future__ import annotations

import hashlib
import shutil
import struct
import wave
from pathlib import Path

import pytest

from server import sources
from server.sources import SourceError

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _write_wav(path: Path, seconds: float = 0.25) -> None:
    """Write a real (silent) 44.1 kHz mono 16-bit WAV, no ffmpeg required."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(44100 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * frames)


def _make_smp(channels: int, samples: list[int]) -> bytes:
    """Build a valid SP-404MK2 ``.SMP`` file (SparkyMK2 RFWV layout)."""
    header = bytearray(512)
    header[0:4] = b"RFWV"
    header[4:8] = struct.pack(">I", len(samples) * 2 + 504)
    header[8:12] = struct.pack(">I", 48000)
    header[12:16] = struct.pack(">I", channels)
    header[16:20] = struct.pack(">I", 16)
    header[0x20:0x30] = hashlib.md5(header[4:20]).digest()
    header[0x30:512] = b"\xa5" * (512 - 0x30)
    pcm = b"".join(struct.pack(">h", s) for s in samples)
    return bytes(header) + pcm


def test_sink_only_devices_have_no_source():
    assert sources.list_source("spotykach") == []
    assert sources.list_source("wave_bard") == []
    assert sources.list_source("fx_wizard") == []
    with pytest.raises(SourceError):
        sources.read_source("spotykach", "whatever")


def test_op1_source_lists_audio_files(tmp_path):
    (tmp_path / "synth" / "user").mkdir(parents=True)
    (tmp_path / "drum" / "user").mkdir(parents=True)
    (tmp_path / "tape").mkdir(parents=True)
    (tmp_path / "album").mkdir(parents=True)
    (tmp_path / "synth" / "user" / "pad.aif").write_bytes(b"x")
    (tmp_path / "drum" / "user" / "kit.aiff").write_bytes(b"x")
    (tmp_path / "tape" / "track1.aif").write_bytes(b"x")
    (tmp_path / "album" / "sideA.aif").write_bytes(b"x")
    (tmp_path / "synth" / "notes.txt").write_text("ignore")
    (tmp_path / "drum" / "user" / "kit.wav").write_bytes(b"x")  # not aif/aiff

    entries = sources.list_source("op1", {"op1_mount": tmp_path})
    rels = {e["rel"] for e in entries}
    assert "synth/user/pad.aif" in rels
    assert "drum/user/kit.aiff" in rels
    assert "tape/track1.aif" in rels
    assert "album/sideA.aif" in rels
    assert "synth/notes.txt" not in rels
    assert "drum/user/kit.wav" not in rels
    kinds = {e["rel"]: e["kind"] for e in entries}
    assert kinds["synth/user/pad.aif"] == "synth"
    assert kinds["album/sideA.aif"] == "album"


def test_op1_source_empty_when_not_mounted():
    assert sources.list_source("op1", {}) == []
    assert sources.list_source("op1", {"op1_mount": None}) == []


def test_sp404_source_lists_only_export(tmp_path):
    (tmp_path / "EXPORT").mkdir(parents=True)
    (tmp_path / "IMPORT").mkdir(parents=True)
    (tmp_path / "EXPORT" / "a.wav").write_bytes(b"x")
    (tmp_path / "EXPORT" / "sub").mkdir()
    (tmp_path / "EXPORT" / "sub" / "b.aiff").write_bytes(b"x")
    (tmp_path / "IMPORT" / "c.wav").write_bytes(b"x")  # sink dir, not source
    (tmp_path / "EXPORT" / "d.txt").write_text("ignore")

    entries = sources.list_source("sp404", {"sp404_card_root": tmp_path})
    rels = {e["rel"] for e in entries}
    assert "EXPORT/a.wav" in rels
    assert "EXPORT/sub/b.aiff" in rels
    assert "IMPORT/c.wav" not in rels
    assert "EXPORT/d.txt" not in rels


def test_sp404_source_empty_without_export(tmp_path):
    assert sources.list_source("sp404", {"sp404_card_root": tmp_path}) == []


def test_sp404_source_lists_smp(tmp_path):
    (tmp_path / "EXPORT" / "SAMPLE").mkdir(parents=True)
    (tmp_path / "EXPORT" / "PROJECT" / "p1" / "SMPL").mkdir(parents=True)
    (tmp_path / "EXPORT" / "SAMPLE" / "a.wav").write_bytes(b"x")
    (tmp_path / "EXPORT" / "PROJECT" / "p1" / "SMPL" / "BANK1-1.smp").write_bytes(b"x")

    entries = sources.list_source("sp404", {"sp404_card_root": tmp_path})
    rels = {e["rel"] for e in entries}
    assert "EXPORT/SAMPLE/a.wav" in rels
    assert "EXPORT/PROJECT/p1/SMPL/BANK1-1.smp" in rels


def test_read_source_decodes_smp(tmp_path):
    card = tmp_path
    (card / "EXPORT" / "PROJECT" / "p1" / "SMPL").mkdir(parents=True)
    smp = _make_smp(2, [0, 1000, -1000, 32767])
    (card / "EXPORT" / "PROJECT" / "p1" / "SMPL" / "BANK1-1.smp").write_bytes(smp)

    data = sources.read_source(
        "sp404", "EXPORT/PROJECT/p1/SMPL/BANK1-1.smp", {"sp404_card_root": card}
    )
    assert data[:4] == b"RIFF"
    # canonical header: float32 tag 0x0003, stereo, 48000 Hz
    assert struct.unpack("<H", data[20:22])[0] == 0x0003
    assert struct.unpack("<H", data[22:24])[0] == 2
    assert struct.unpack("<I", data[24:28])[0] == 48000


def test_read_source_decodes_to_canonical(tmp_path):
    mount = tmp_path / "op1"
    _write_wav(mount / "synth" / "user" / "pad.aif")
    data = sources.read_source("op1", "synth/user/pad.aif", {"op1_mount": mount})
    assert data[:4] == b"RIFF"
    # canonical header: float32 tag 0x0003, stereo, 48000 Hz
    assert struct.unpack("<H", data[20:22])[0] == 0x0003
    assert struct.unpack("<H", data[22:24])[0] == 2
    assert struct.unpack("<I", data[24:28])[0] == 48000


def test_read_source_rejects_path_traversal(tmp_path):
    mount = tmp_path / "op1"
    (mount / "synth").mkdir(parents=True)
    (tmp_path / "escape.wav").write_bytes(b"x")
    with pytest.raises(SourceError):
        sources.read_source("op1", "../escape.wav", {"op1_mount": mount})


def test_read_source_rejects_missing(tmp_path):
    mount = tmp_path / "op1"
    (mount / "synth").mkdir(parents=True)
    with pytest.raises(SourceError):
        sources.read_source("op1", "synth/nope.aif", {"op1_mount": mount})
