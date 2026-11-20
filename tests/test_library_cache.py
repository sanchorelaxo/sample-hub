"""Library cache: canonical samples and rhythm patterns.

Milestone 9 backing store for ``GET /api/library``. The library is the host
cache the panes drag from: a ``samples/`` dir of canonical 48 kHz stereo
float32 WAVs and a ``rhythms/`` dir of step/gate JSON. Every read/write runs
against an injected ``library_dir`` (tmp_path), so no test touches the live
cache, and the samples are real ffmpeg output decoded back with struct.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from server.library import (
    CANONICAL_CHANNELS,
    CANONICAL_RATE,
    FLOAT_FORMAT_TAG,
    LibraryError,
    add_rhythm,
    add_sample,
    default_library_dir,
    list_entries,
    resolve,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def make_wav(path: Path, *, rate: int, channels: int, duration: float) -> Path:
    """Write a real 16-bit WAV with ffmpeg's sine source."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def parse_wav(raw: bytes) -> dict:
    """Decode a RIFF/WAVE header into its fields."""
    assert raw[:4] == b"RIFF"
    assert raw[8:12] == b"WAVE"
    assert raw[12:16] == b"fmt "
    fmt_size = struct.unpack("<I", raw[16:20])[0]
    audio_format, channels, rate, _byte_rate, _block_align, bits = struct.unpack(
        "<HHIIHH", raw[20:36]
    )
    return {"format": audio_format, "channels": channels, "rate": rate, "bits": bits}


def gates() -> list[bool]:
    return [True, False, True, False, True, False, True, False,
            True, False, True, False, True, False, True, False]


def test_default_library_dir_tracks_data_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_HUB_DATA", str(tmp_path / "hub-data"))
    assert default_library_dir() == (tmp_path / "hub-data" / "library")


def test_add_sample_normalizes_to_canonical_and_lists(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=44100, channels=1, duration=0.25)
    library_dir = tmp_path / "library"

    entry = add_sample(src, name="kick.wav", library_dir=library_dir)

    assert entry["type"] == "sample"
    assert entry["id"] == "kick.wav"
    assert entry["name"] == "kick.wav"
    stored = library_dir / "samples" / "kick.wav"
    wav = parse_wav(stored.read_bytes())
    assert wav["format"] == FLOAT_FORMAT_TAG
    assert wav["channels"] == CANONICAL_CHANNELS
    assert wav["rate"] == CANONICAL_RATE
    assert wav["bits"] == 32

    listing = list_entries(library_dir=library_dir)
    assert [s["id"] for s in listing["samples"]] == ["kick.wav"]
    assert listing["rhythms"] == []


def test_add_sample_same_name_is_additive(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=48000, channels=2, duration=0.2)
    library_dir = tmp_path / "library"

    first = add_sample(src, name="snare.wav", library_dir=library_dir)
    second = add_sample(src, name="snare.wav", library_dir=library_dir)

    assert first["id"] == "snare.wav"
    assert second["id"] == "snare-2.wav"
    assert (library_dir / "samples" / "snare.wav").is_file()
    assert (library_dir / "samples" / "snare-2.wav").is_file()
    assert len(list_entries(library_dir=library_dir)["samples"]) == 2


def test_resolve_sample_returns_canonical_bytes(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=22050, channels=1, duration=0.2)
    library_dir = tmp_path / "library"
    add_sample(src, name="hat.wav", library_dir=library_dir)

    kind, payload = resolve("hat.wav", library_dir=library_dir)

    assert kind == "sample"
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"


def test_add_rhythm_stores_step_gate_and_resolves(tmp_path):
    library_dir = tmp_path / "library"
    step_data = {"steps": 16, "gates": gates()}

    entry = add_rhythm(step_data, name="hats", library_dir=library_dir)

    assert entry["type"] == "rhythm"
    assert entry["id"] == "hats.json"
    stored = library_dir / "rhythms" / "hats.json"
    assert json.loads(stored.read_text()) == step_data

    kind, payload = resolve("hats.json", library_dir=library_dir)
    assert kind == "rhythm"
    assert payload == step_data


def test_rhythm_same_name_is_additive(tmp_path):
    library_dir = tmp_path / "library"
    step_data = {"steps": 8, "gates": [True, False, True, False, True, False, True, False]}

    first = add_rhythm(step_data, name="pulse", library_dir=library_dir)
    second = add_rhythm(step_data, name="pulse", library_dir=library_dir)

    assert first["id"] == "pulse.json"
    assert second["id"] == "pulse-2.json"
    assert len(list_entries(library_dir=library_dir)["rhythms"]) == 2


def test_list_entries_partitions_samples_and_rhythms(tmp_path):
    src = make_wav(tmp_path / "in.wav", rate=48000, channels=2, duration=0.2)
    library_dir = tmp_path / "library"
    add_sample(src, name="kick.wav", library_dir=library_dir)
    add_rhythm({"steps": 16, "gates": gates()}, name="hats", library_dir=library_dir)

    listing = list_entries(library_dir=library_dir)

    assert [s["id"] for s in listing["samples"]] == ["kick.wav"]
    assert [r["id"] for r in listing["rhythms"]] == ["hats.json"]


def test_resolve_unknown_id_raises_library_error(tmp_path):
    with pytest.raises(LibraryError):
        resolve("missing.wav", library_dir=tmp_path / "library")
