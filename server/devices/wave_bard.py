"""Wave Bard stager — assemble a firmware draft (samples + rhythms + scales).

Wave Bard is dual-purpose: its loader carries samples *and* rhythms *and* scales
in one firmware. ``build_draft`` takes the full editor state (quality, process
samples toggles, banks, scales, rhythms, sequencer length) and emits a "ready to
flash" draft — a JSON manifest plus staged sample WAVs — to be handed to the
Bastl loader for the actual ``.uf2``. No device I/O here, so the draft is fully
testable without hardware.

Each sample is converted through ``convert.to_wave_bard_processed`` (mono float32
WAV at the selected quality rate, with the selected fade/trim/maximize toggles
applied) and written into the staging directory under a sanitized leaf name.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from server.convert import to_wave_bard_pcm16, to_wave_bard_processed
from server.devices import READY_TO_FLASH
from server.pattern import (
    PROCESS_OPTIONS,
    WAVE_BARD_DEFAULT_LENGTH,
    WAVE_BARD_DEFAULT_RATE,
    validate_wave_bard,
)
from server.uf2 import UF2_FAMILY_ID, USER_DATA_BEGIN, generate_uf2, merge_uf2

# Bastl's waveBardBinaryFormat.js container constants.
WAVE_BARD_MAGIC = b"k2wb"
WAVE_BARD_END = b"ahoj"
WAVE_BARD_HEADER_SIZE = 20
WAVE_BARD_BIT_DEPTH = 16
WAVE_BARD_DEFAULT_COLOR = "#FFFFFF"


def _safe_leaf(filename: str, suffix: str) -> str:
    stem = Path(filename).stem
    kept = "".join(ch if (ch.isalnum() or ch in " -_.") else "_" for ch in stem)
    stem = kept.strip(" .") or "sample"
    return f"{stem}{suffix}"


def _with_id(item: dict, prefix: str, index: int) -> dict:
    return {**item, "id": item.get("id") or f"{prefix}{index + 1}"}


def build_draft(state: dict, out_dir) -> dict:
    """Full editor state -> a "ready to flash" Wave Bard draft.

    ``state`` carries ``sampleRate`` (one of the loader's quality rates),
    ``audioProcessing`` (a dict of ``PROCESS_OPTIONS`` booleans), ``banks`` (a
    list of ``{"id"?, "name", "samples": [{"id"?, "name", "data": canonical
    WAV bytes}]}``), ``scales`` (``{"id"?, "name", "semitones"}``), ``rhythms``
    (``{"id"?, "name", "steps"}``) and ``sequencerLength``. Raises ``ValueError``
    when the state fails the loader's limits. ``out_dir`` receives the staged
    ``<name>.wav`` files alongside ``manifest.json``.
    """
    err = validate_wave_bard(state)
    if err:
        raise ValueError(err)

    sample_rate = int(state.get("sampleRate", WAVE_BARD_DEFAULT_RATE))
    process = tuple(p for p in PROCESS_OPTIONS if state.get("audioProcessing", {}).get(p))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    banks = []
    for bi, bank in enumerate(state.get("banks", [])):
        samples = []
        for si, sample in enumerate(bank.get("samples", [])):
            leaf = _safe_leaf(sample["name"], ".wav")
            (out / leaf).write_bytes(to_wave_bard_processed(sample["data"], sample_rate, process))
            samples.append(
                _with_id({"name": sample["name"], "file": leaf}, "s", si)
            )
        banks.append(_with_id({"name": bank.get("name", f"bank {bi + 1}"), "samples": samples}, "b", bi))

    scales = [
        _with_id({"name": s.get("name", f"scale {i + 1}"), "semitones": int(s["semitones"])}, "sc", i)
        for i, s in enumerate(state.get("scales", []))
    ]
    rhythms = [
        _with_id({"name": r.get("name", f"rhythm {i + 1}"), "steps": int(r["steps"])}, "r", i)
        for i, r in enumerate(state.get("rhythms", []))
    ]

    draft = {
        "state": READY_TO_FLASH,
        "target": "wave-bard",
        "format": "wave-bard-loader",
        "sampleRate": sample_rate,
        "audioProcessing": {p: bool(state.get("audioProcessing", {}).get(p)) for p in PROCESS_OPTIONS},
        "sequencerLength": int(state.get("sequencerLength", WAVE_BARD_DEFAULT_LENGTH)),
        "banks": banks,
        "scales": scales,
        "rhythms": rhythms,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True))
    draft["manifest_path"] = str(manifest_path)
    return draft


def _u32_le(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def _label8(name: str) -> bytes:
    """8-byte UTF-8 label, truncated and NUL-padded (mirrors writeString)."""
    return name.encode("utf-8", "replace")[:8].ljust(8, b"\x00")


def _color_rgb(color: str) -> tuple[int, int, int]:
    hexpart = (color or "").lstrip("#")
    if len(hexpart) < 6 or any(c not in "0123456789abcdefABCDEF" for c in hexpart[:6]):
        return (0xFF, 0xFF, 0xFF)
    r = int(hexpart[0:2], 16)
    g = int(hexpart[2:4], 16)
    b = int(hexpart[4:6], 16)
    return (r, g, b)


def encode_binary(
    banks: list[dict],
    scales: list[int],
    rhythms: list[int],
    sequencer_length: int,
    sample_rate: int,
    bit_depth: int = WAVE_BARD_BIT_DEPTH,
) -> bytes:
    """Assemble the ``k2wb`` user-data binary (port of WaveBardBinaryWriter).

    ``banks`` is ``[{"label", "color", "samples": [{"label", "channels",
    "data": Int16 LE PCM bytes}]}]``; ``scales``/``rhythms`` are the raw u32
    semitone/steps values; ``sequencer_length`` and ``sample_rate`` are stored in
    the header. Layout: magic(4) + file size u32 LE + sample rate u32 LE +
    bitDepth/nBanks/samplesPerBank/nScales/nRhythms/sequencerLength/reserved u8s
    (header 20 bytes), then scales (u32 LE each), rhythms (u32 LE each), then per
    bank a 12-byte header (label + RGB + reserved) and per sample a 16-byte
    header (size+pad u32 LE + channels u8 + label + 3 reserved) followed by the
    Int16 data padded to 4 bytes; finally the 4-byte ``ahoj`` end marker.
    """
    sample_counts = {len(b.get("samples", [])) for b in banks}
    if len(sample_counts) > 1:
        raise ValueError("all banks must have the same number of samples")
    samples_per_bank = next(iter(sample_counts), 0)

    def sample_parts() -> list[tuple[bytes, int]]:
        parts = []
        for bank in banks:
            for sample in bank.get("samples", []):
                data = bytes(sample["data"])
                padding = (4 - len(data) % 4) % 4
                parts.append((data, padding))
        return parts

    total = (
        WAVE_BARD_HEADER_SIZE
        + 4 * len(scales)
        + 4 * len(rhythms)
        + 12 * len(banks)
        + sum(16 + len(data) + padding for data, padding in sample_parts())
        + 4
    )

    out = bytearray()
    out += WAVE_BARD_MAGIC
    out += _u32_le(total)
    out += _u32_le(sample_rate)
    out += bytes(
        [
            bit_depth & 0xFF,
            len(banks) & 0xFF,
            samples_per_bank & 0xFF,
            len(scales) & 0xFF,
            len(rhythms) & 0xFF,
            sequencer_length & 0xFF,
            0,
            0,
        ]
    )
    for scale in scales:
        out += _u32_le(scale)
    for rhythm in rhythms:
        out += _u32_le(rhythm)
    for bank in banks:
        r, g, b = _color_rgb(bank.get("color") or WAVE_BARD_DEFAULT_COLOR)
        out += _label8(bank.get("label", ""))
        out += bytes([r, g, b, 0])
        for sample in bank.get("samples", []):
            data = bytes(sample["data"])
            padding = (4 - len(data) % 4) % 4
            out += _u32_le(len(data) + padding)
            out += bytes([int(sample.get("channels", 1)) & 0xFF])
            out += _label8(sample.get("label", ""))
            out += b"\x00\x00\x00"
            out += data
            out += b"\x00" * padding
    out += WAVE_BARD_END
    return bytes(out)


def build_binary(state: dict) -> bytes:
    """Full editor state -> the ``k2wb`` user-data binary (Int16 samples)."""
    err = validate_wave_bard(state)
    if err:
        raise ValueError(err)
    sample_rate = int(state.get("sampleRate", WAVE_BARD_DEFAULT_RATE))
    process = tuple(p for p in PROCESS_OPTIONS if state.get("audioProcessing", {}).get(p))
    sequencer_length = int(state.get("sequencerLength", WAVE_BARD_DEFAULT_LENGTH))

    banks = []
    for bi, bank in enumerate(state.get("banks", [])):
        samples = []
        for si, sample in enumerate(bank.get("samples", [])):
            samples.append(
                {
                    "label": sample.get("label") or sample.get("name", f"sample {si + 1}"),
                    "channels": 1,
                    "data": to_wave_bard_pcm16(sample["data"], sample_rate, process),
                }
            )
        banks.append(
            {
                "label": bank.get("label") or bank.get("name", f"bank {bi + 1}"),
                "color": bank.get("color") or WAVE_BARD_DEFAULT_COLOR,
                "samples": samples,
            }
        )

    scales = [int(s["semitones"]) for s in state.get("scales", [])]
    rhythms = [int(r["steps"]) for r in state.get("rhythms", [])]
    return encode_binary(banks, scales, rhythms, sequencer_length, sample_rate)


def build_uf2(state: dict, base_firmware: bytes) -> bytes:
    """Editor state -> a merged, validated ``.uf2`` image.

    Mirrors the loader's pipeline: build the ``k2wb`` binary, wrap it in UF2
    blocks at ``USER_DATA_BEGIN``, then merge onto ``base_firmware`` (a Bastl
    ``*-no-samples.uf2``) with ``fill_gaps=True`` so the flash range is mapped
    contiguously. Returns the full image bytes; nothing is written to hardware.
    """
    samples_uf2 = generate_uf2(UF2_FAMILY_ID, USER_DATA_BEGIN, build_binary(state))
    return merge_uf2([base_firmware, samples_uf2], fill_gaps=True)


__all__ = ["build_draft", "build_binary", "build_uf2", "encode_binary"]
