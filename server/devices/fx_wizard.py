"""FX Wizard stager — rhythm pattern + step count firmware draft.

FX Wizard is a rhythm-only destination (a 9-mode FX processor); it never stores
audio samples. v1 emits a "ready to flash" Chamber draft (rhythm + step count)
mapped by ``convert.fx_wizard_rhythm``, to be baked into a ``.uf2`` by the
Chamber flow (firmware 1.6+). No device I/O, so the draft is fully testable
without hardware.
"""
from __future__ import annotations

import json
from pathlib import Path

from server.convert import fx_wizard_rhythm
from server.devices import READY_TO_FLASH


def build_draft(step_data: dict, out_dir=None) -> dict:
    """Step/gate JSON -> a "ready to flash" Chamber rhythm draft.

    With ``out_dir`` the draft is also written to ``<out_dir>/manifest.json``.
    """
    draft = {
        "state": READY_TO_FLASH,
        "target": "fx-wizard",
        "format": "chamber",
        "rhythm": fx_wizard_rhythm(step_data),
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True))
        draft["manifest_path"] = str(manifest_path)
    return draft


__all__ = ["build_draft"]
