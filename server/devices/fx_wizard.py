"""FX Wizard stager — rhythm set + sequencer length firmware draft.

FX Wizard is a rhythm-only destination (a 9-mode FX processor); it never stores
audio samples. ``build_draft`` takes the full editor state (sequencer length plus
a list of rhythms, each a ``steps`` bitmask — see server.pattern) and emits a
"ready to flash" Chamber draft, to be baked into a ``.uf2`` by the Chamber flow
(firmware 1.6+). No device I/O, so the draft is fully testable without hardware.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.devices import READY_TO_FLASH
from server.pattern import FX_WIZARD_DEFAULT_LENGTH, validate_fx_wizard


def build_draft(state: dict, out_dir=None) -> dict:
    """Full editor state -> a "ready to flash" Chamber rhythm-set draft.

    ``state`` carries ``sequencerLength`` and ``rhythms`` (a list of
    ``{"id"?, "name", "steps"}``). Raises ``ValueError`` when the rhythm set
    fails the loader's 3..64 limits. With ``out_dir`` the draft is also written
    to ``<out_dir>/manifest.json``.
    """
    rhythms = state.get("rhythms", [])
    err = validate_fx_wizard(rhythms)
    if err:
        raise ValueError(err)

    rhythms = [
        {
            "id": r.get("id") or f"r{i + 1}",
            "name": r.get("name", f"rhythm {i + 1}"),
            "steps": int(r["steps"]),
        }
        for i, r in enumerate(rhythms)
    ]

    draft = {
        "state": READY_TO_FLASH,
        "target": "fx-wizard",
        "format": "chamber",
        "sequencerLength": int(state.get("sequencerLength", FX_WIZARD_DEFAULT_LENGTH)),
        "rhythms": rhythms,
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True))
        draft["manifest_path"] = str(manifest_path)
    return draft


__all__ = ["build_draft"]
