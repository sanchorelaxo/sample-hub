"""Canonical normalizer: any supported WAV -> 48 kHz stereo float32 WAV.

The hub normalizes every incoming audio sample once, to the canonical working
form, before any destination transform runs (SPEC.md, "The Conversion Matrix").
The canonical form matches the ``wav_builder`` output that spottytube-ext
already produces for the Spotykach: 48 kHz, stereo, 32-bit IEEE float, written
with the plain IEEE-float format tag (``0x0003``) rather than
``WAVE_FORMAT_EXTENSIBLE`` (``0xFFFE``), because the Spotykach firmware
(libDaisy) only recognizes the plain float tag.

Mono upmix note: a mono source is upmixed to stereo by ffmpeg, which applies a
-3dB pan-law attenuation per channel so the total power stays constant rather
than doubling. A mono sample is therefore ~0.707x its original amplitude on
each channel after normalization; this is intentional and mirrors how every
mono->stereo path in the hub behaves.

Concurrency contract: ``normalize`` is stateless and re-entrant. ffmpeg is NOT
a singleton here -- every call spawns its own subprocess and holds no shared
lock, temp file, or module-level state, so parallel jobs (one per instrument)
never contend. The decode runs straight to raw float32 on stdout and the bytes
are captured in memory, so there is no shared temp path for concurrent calls to
clobber.

Milestone 1 scope is WAV input. ffmpeg decodes regardless of container, so the
same path will accept the other source formats (AIFF, MP3, OGG, ...) when the
library cache grows to feed them in; that acceptance is asserted in a later
milestone.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

CANONICAL_RATE = 48000
CANONICAL_CHANNELS = 2
FLOAT_FORMAT_TAG = 0x0003  # IEEE float; libDaisy/Spotykach read this tag


class NormalizeError(RuntimeError):
    """Raised when ffmpeg cannot decode the source into the canonical form."""


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def normalize(src: Path | str, dst: Path | str) -> Path:
    """Normalize ``src`` to the canonical 48 kHz stereo float32 WAV at ``dst``.

    Decodes the source with a fresh ffmpeg subprocess straight to raw
    interleaved little-endian float32 on stdout, then writes the canonical WAV
    header around the captured bytes. No shared temp files and no global lock:
    concurrent calls are fully independent.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-i", str(src),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ar", str(CANONICAL_RATE),
        "-ac", str(CANONICAL_CHANNELS),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise NormalizeError(f"ffmpeg failed for {src.name}: {detail[-400:]}")
    _write_float_wav(dst, result.stdout)
    return dst


def _write_float_wav(path: Path, pcm: bytes) -> None:
    """Write a canonical IEEE-float WAV (format tag 0x0003, 16-byte fmt)."""
    bits = 32
    block_align = CANONICAL_CHANNELS * bits // 8
    byte_rate = CANONICAL_RATE * block_align
    fmt_len = 16
    data_len = len(pcm)
    riff_size = 4 + (8 + fmt_len) + (8 + data_len)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", riff_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", fmt_len))
        f.write(struct.pack("<H", FLOAT_FORMAT_TAG))
        f.write(struct.pack("<H", CANONICAL_CHANNELS))
        f.write(struct.pack("<I", CANONICAL_RATE))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits))
        f.write(b"data")
        f.write(struct.pack("<I", data_len))
        f.write(pcm)


LIBRARY_DATA_ENV = "SAMPLE_HUB_DATA"


class LibraryError(LookupError):
    """Raised when a library entry id cannot be resolved."""


def default_library_dir() -> Path:
    """The library root: ``$SAMPLE_HUB_DATA/library`` or ``~/.sample-hub/library``."""
    base = Path(os.environ.get(LIBRARY_DATA_ENV, "~/.sample-hub")).expanduser()
    return base / "library"


def _sanitize_stem(name: str, fallback: str = "sample") -> str:
    """ASCII filename stem from a label: alnum, underscore and hyphen only."""
    stem = Path(name).stem
    cleaned = "".join(c if c.isalnum() or c in "_-" else "_" for c in stem)
    cleaned = cleaned.strip("_").lower()
    return cleaned or fallback


def _next_free_name(directory: Path, stem: str, suffix: str) -> str:
    """First unused ``stem``, then ``stem-2``, ``stem-3``, ... (additive, no deletes)."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = f"{stem}{suffix}"
    index = 2
    while (directory / candidate).exists():
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def add_sample(src: Path | str, name: str | None = None, library_dir: Path | None = None) -> dict:
    """Normalize ``src`` to canonical and store it in the cache as a sample entry."""
    library_dir = library_dir or default_library_dir()
    display_name = name or Path(src).name
    leaf = _next_free_name(library_dir / "samples", _sanitize_stem(display_name), ".wav")
    target = library_dir / "samples" / leaf
    normalize(src, target)
    return {
        "id": leaf,
        "name": leaf,
        "type": "sample",
        "bytes": target.stat().st_size,
        "rate": CANONICAL_RATE,
        "channels": CANONICAL_CHANNELS,
    }


def add_rhythm(step_data: dict, name: str, library_dir: Path | None = None) -> dict:
    """Store a step/gate dict as a rhythm entry in the cache."""
    library_dir = library_dir or default_library_dir()
    leaf = _next_free_name(library_dir / "rhythms", _sanitize_stem(name, fallback="rhythm"), ".json")
    target = library_dir / "rhythms" / leaf
    target.write_text(json.dumps(step_data, sort_keys=True))
    return {
        "id": leaf,
        "name": leaf,
        "type": "rhythm",
        "steps": step_data.get("steps"),
        "gates": step_data.get("gates", []),
    }


def list_entries(library_dir: Path | None = None) -> dict:
    """Every cached entry, split into ``samples`` and ``rhythms`` (metadata only)."""
    library_dir = library_dir or default_library_dir()
    samples = []
    for path in sorted((library_dir / "samples").glob("*.wav")):
        samples.append(
            {
                "id": path.name,
                "name": path.name,
                "type": "sample",
                "bytes": path.stat().st_size,
                "rate": CANONICAL_RATE,
                "channels": CANONICAL_CHANNELS,
            }
        )
    rhythms = []
    for path in sorted((library_dir / "rhythms").glob("*.json")):
        try:
            step_data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rhythms.append(
            {
                "id": path.name,
                "name": path.name,
                "type": "rhythm",
                "steps": step_data.get("steps"),
                "gates": step_data.get("gates", []),
            }
        )
    return {"samples": samples, "rhythms": rhythms}


def resolve(source_id: str, library_dir: Path | None = None) -> tuple[str, bytes | dict]:
    """Resolve a cache id to ``("sample", canonical_bytes)`` or ``("rhythm", step_data)``."""
    library_dir = library_dir or default_library_dir()
    sample_path = library_dir / "samples" / source_id
    rhythm_path = library_dir / "rhythms" / source_id
    if sample_path.is_file():
        return "sample", sample_path.read_bytes()
    if rhythm_path.is_file():
        return "rhythm", json.loads(rhythm_path.read_text())
    raise LibraryError(source_id)
