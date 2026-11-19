"""Wave Bard stager — assemble a firmware draft (samples + rhythms + scales).

Wave Bard is dual-purpose: its loader carries samples *and* rhythms *and* scales
in one firmware. v1 emits a "ready to flash" draft — a JSON manifest plus staged
sample WAVs — to be handed to the Bastl loader for the actual ``.uf2``. No
device I/O here, so the draft is fully testable without hardware.

Each sample is converted through the matrix's ``to_wave_bard`` (downmix -> tune
to C -> trim silence -> mono 48 kHz float32 WAV) and written into the staging
directory under a sanitized leaf name.
"""
from __future__ import annotations

import json
from pathlib import Path

from server.convert import to_wave_bard, wave_bard_rhythm
from server.devices import READY_TO_FLASH


def _safe_leaf(filename: str, suffix: str) -> str:
    stem = Path(filename).stem
    kept = "".join(ch if (ch.isalnum() or ch in " -_.") else "_" for ch in stem)
    stem = kept.strip(" .") or "sample"
    return f"{stem}{suffix}"


def build_draft(bank_name: str, samples, rhythms, scales, out_dir) -> dict:
    """Stage a bank: convert samples to mono 48k f32 WAVs and write a manifest.

    ``samples`` is a list of ``{"name": str, "data": bytes}``; ``rhythms`` a
    list of step/gate dicts; ``scales`` a list passed through. Returns the draft
    dict (JSON-serializable) with ``state == "ready_to_flash"`` and writes it to
    ``<out_dir>/manifest.json`` alongside the staged ``<name>.wav`` files.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    staged = []
    for sample in samples:
        leaf = _safe_leaf(sample["name"], ".wav")
        (out / leaf).write_bytes(to_wave_bard(sample["data"]))
        staged.append({"name": sample["name"], "file": leaf})

    draft = {
        "state": READY_TO_FLASH,
        "bank": bank_name,
        "samples": staged,
        "rhythms": [wave_bard_rhythm(r) for r in rhythms],
        "scales": list(scales),
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True))
    draft["manifest_path"] = str(manifest_path)
    return draft


__all__ = ["build_draft"]
