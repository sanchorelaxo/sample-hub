"""Convert dispatch + per-instrument job queue.

Milestone 9 core. ``run_conversion`` maps a cached source (sample bytes or a
step/gate dict) plus a target instrument to the right writer, refusing wrong
asset types inline with the SPEC.md §6 reasons. ``JobQueue`` runs those jobs
with one worker thread per instrument so different instruments convert in
parallel while drops on the same instrument stay serialized.

Every transfer here is device-free: SP-404 writes to an injected ``card_root``,
Wave Bard / FX Wizard stage into an injected ``out_dir``. The OP-1 and
Spotykach paths need hardware and are only exercised under ``--real-devices``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from server import jobs
from server.jobs import JobQueue, RejectError, reject_reason, run_conversion, run_draft
from server.library import normalize
from server.pattern import pattern_to_steps

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def make_canonical_wav(tmp_path: Path, name: str) -> bytes:
    """A real canonical 48 kHz stereo float32 WAV's bytes."""
    src = tmp_path / f"{name}-src.wav"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
            "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    dst = tmp_path / f"{name}-canonical.wav"
    normalize(src, dst)
    return dst.read_bytes()


def gates() -> list[bool]:
    return [True, False, True, False, True, False, True, False,
            True, False, True, False, True, False, True, False]


def wait_for(queue: JobQueue, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = queue.status(job_id)
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_reject_reason_matrix():
    assert reject_reason("sample", "fx_wizard") == "rhythms only — no sample input"
    assert reject_reason("rhythm", "sp404") == "samples only"
    assert reject_reason("rhythm", "op1") == "samples only"
    assert reject_reason("rhythm", "spotykach") == "samples only"
    assert reject_reason("sample", "sp404") is None
    assert reject_reason("sample", "wave_bard") is None
    assert reject_reason("rhythm", "wave_bard") is None
    assert reject_reason("rhythm", "fx_wizard") is None
    assert reject_reason("sample", "bogus") is not None


def test_run_conversion_refuses_wrong_type(tmp_path):
    wav = make_canonical_wav(tmp_path, "kick")
    with pytest.raises(RejectError) as exc:
        run_conversion("kick.wav", "sample", wav, "fx_wizard")
    assert "no sample input" in str(exc.value)


def test_run_conversion_sample_to_sp404(tmp_path):
    wav = make_canonical_wav(tmp_path, "kick")
    card_root = tmp_path / "card"

    result = run_conversion(
        "kick.wav", "sample", wav, "sp404",
        roots={"sp404_card_root": card_root},
    )

    assert result["target"] == "sp404"
    assert result["result"]["rel"] == "IMPORT/kick.wav"
    assert (card_root / "IMPORT" / "kick.wav").is_file()


def test_run_conversion_rejects_editor_only_target():
    step_data = {"steps": 16, "gates": gates()}
    with pytest.raises(RejectError) as exc:
        run_conversion("hats", "rhythm", step_data, "fx_wizard")
    assert "run_draft" in str(exc.value)


def test_run_draft_fx_wizard(tmp_path):
    state = {
        "sequencerLength": 8,
        "rhythms": [
            {"name": "one", "steps": pattern_to_steps([0, 4], 8)},
            {"name": "two", "steps": pattern_to_steps([2, 6], 8)},
            {"name": "three", "steps": pattern_to_steps([1, 3, 5], 8)},
        ],
    }
    out_dir = tmp_path / "drafts"

    result = run_draft("fx_wizard", state, roots={"fx_wizard_out": out_dir})

    draft = result["result"]
    assert draft["state"] == "ready_to_flash"
    assert draft["target"] == "fx-wizard"
    assert len(draft["rhythms"]) == 3
    assert (out_dir / "manifest.json").is_file()


def test_run_draft_wave_bard(tmp_path):
    wav = make_canonical_wav(tmp_path, "kick")
    state = {
        "sampleRate": 44100,
        "audioProcessing": {
            "fadeIn": False, "fadeOut": False, "trimQuiet": False, "maximize": False,
        },
        "sequencerLength": 16,
        "banks": [
            {"name": "a", "samples": [
                {"name": "kick", "data": wav},
                {"name": "snare", "data": wav},
                {"name": "hat", "data": wav},
            ]},
        ],
        "scales": [
            {"name": "major", "semitones": 0b101011010101},
            {"name": "minor", "semitones": 0b101101011010},
            {"name": "pent", "semitones": 0b101010010100},
        ],
        "rhythms": [
            {"name": "one", "steps": pattern_to_steps([0, 4, 8, 12], 16)},
            {"name": "two", "steps": pattern_to_steps([2, 6, 10, 14], 16)},
            {"name": "three", "steps": pattern_to_steps([0, 8], 16)},
        ],
    }
    out_dir = tmp_path / "drafts"

    result = run_draft("wave_bard", state, roots={"wave_bard_out": out_dir})

    draft = result["result"]
    assert draft["state"] == "ready_to_flash"
    assert draft["target"] == "wave-bard"
    assert len(draft["banks"]) == 1
    assert len(draft["banks"][0]["samples"]) == 3
    assert len(draft["scales"]) == 3
    assert len(draft["rhythms"]) == 3
    assert (out_dir / "manifest.json").is_file()


def test_run_draft_unknown_target():
    with pytest.raises(RejectError):
        run_draft("op1", {})


def test_job_queue_serializes_per_instrument(tmp_path):
    wav = make_canonical_wav(tmp_path, "kick")
    card_root = tmp_path / "card"
    queue = JobQueue()
    try:
        first = queue.submit(
            "sp404", run_conversion, "kick.wav", "sample", wav, "sp404",
            {}, {"sp404_card_root": card_root},
        )
        second = queue.submit(
            "sp404", run_conversion, "kick.wav", "sample", wav, "sp404",
            {}, {"sp404_card_root": card_root},
        )

        one = wait_for(queue, first)
        two = wait_for(queue, second)

        assert one["status"] == "done"
        assert two["status"] == "done"
        assert one["result"]["result"]["rel"] == "IMPORT/kick.wav"
        assert two["result"]["result"]["rel"] == "IMPORT/kick-2.wav"
        assert (card_root / "IMPORT" / "kick.wav").is_file()
        assert (card_root / "IMPORT" / "kick-2.wav").is_file()
    finally:
        queue.shutdown()


def test_job_queue_records_error(tmp_path):
    wav = make_canonical_wav(tmp_path, "kick")
    queue = JobQueue()
    try:
        job_id = queue.submit(
            "fx_wizard", run_conversion, "kick.wav", "sample", wav, "fx_wizard",
            {}, {},
        )

        state = wait_for(queue, job_id)

        assert state["status"] == "error"
        assert "no sample input" in state["error"]
    finally:
        queue.shutdown()


def test_job_status_unknown_id_raises():
    queue = JobQueue()
    try:
        with pytest.raises(KeyError):
            queue.status("job-999")
    finally:
        queue.shutdown()
