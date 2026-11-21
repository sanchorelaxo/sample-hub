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
from pathlib import Path

from server.convert import to_wave_bard_processed
from server.devices import READY_TO_FLASH
from server.pattern import (
    PROCESS_OPTIONS,
    WAVE_BARD_DEFAULT_LENGTH,
    WAVE_BARD_DEFAULT_RATE,
    validate_wave_bard,
)


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


__all__ = ["build_draft"]
