"""Milestone 7 — FX Wizard stager: rhythm + step count firmware draft.

FX Wizard is a rhythm-only destination (9-mode FX processor); it never stores
audio samples. The draft maps a step/gate pattern into Chamber rhythm/step
format with a "ready to flash" state. No device I/O, so no hardware is needed.
"""
from __future__ import annotations

import json

from server.devices.fx_wizard import READY_TO_FLASH, build_draft


def test_build_draft_is_chamber_rhythm(tmp_path):
    draft = build_draft(
        {"steps": 16, "gates": [1, 0, 0, 0, 1, 0, 0, 0]},
        tmp_path,
    )
    assert draft["state"] == READY_TO_FLASH
    assert draft["target"] == "fx-wizard"
    assert draft["format"] == "chamber"
    assert draft["rhythm"] == {"step_count": 16, "pattern": [0, 4]}
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["state"] == READY_TO_FLASH
    assert manifest["rhythm"]["step_count"] == 16


def test_build_draft_without_out_dir_is_in_memory():
    draft = build_draft({"steps": 8, "gates": [0, 1, 0, 1]})
    assert draft["state"] == READY_TO_FLASH
    assert draft["rhythm"] == {"step_count": 8, "pattern": [1, 3]}
    assert "manifest_path" not in draft
