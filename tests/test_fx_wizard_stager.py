"""Milestone 7 — FX Wizard stager: rhythm set + sequencer length draft.

FX Wizard is a rhythm-only destination (9-mode FX processor); it never stores
audio samples. The draft maps an editor state (sequencer length + a list of
``steps`` bitmasks) into a "ready to flash" Chamber draft. No device I/O, so no
hardware is needed.
"""

from __future__ import annotations

import json

import pytest

from server.devices.fx_wizard import READY_TO_FLASH, build_draft
from server.pattern import pattern_to_steps


def make_state(n: int = 3, length: int = 8) -> dict:
    rhythms = [
        {
            "id": f"r{i}",
            "name": f"rhythm {i}",
            "steps": pattern_to_steps([i % length], length),
        }
        for i in range(n)
    ]
    return {"sequencerLength": length, "rhythms": rhythms}


def test_build_draft_is_chamber_rhythm_set(tmp_path):
    draft = build_draft(make_state(), tmp_path)
    assert draft["state"] == READY_TO_FLASH
    assert draft["target"] == "fx-wizard"
    assert draft["format"] == "chamber"
    assert draft["sequencerLength"] == 8
    assert [r["id"] for r in draft["rhythms"]] == ["r0", "r1", "r2"]
    assert draft["rhythms"][0]["steps"] != 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["state"] == READY_TO_FLASH
    assert manifest["sequencerLength"] == 8


def test_build_draft_without_out_dir_is_in_memory():
    draft = build_draft(make_state())
    assert draft["state"] == READY_TO_FLASH
    assert draft["sequencerLength"] == 8
    assert "manifest_path" not in draft


def test_build_draft_requires_at_least_three_rhythms(tmp_path):
    with pytest.raises(ValueError):
        build_draft(make_state(n=2), tmp_path)


def test_build_draft_rejects_empty_rhythm(tmp_path):
    state = make_state()
    state["rhythms"][1]["steps"] = 0
    with pytest.raises(ValueError):
        build_draft(state, tmp_path)
