# Sample Hub

> One localhost web interface that moves a sample between every USB instrument
> on the desk, doing the format conversion for each destination so a drop
> always lands in a file the target can actually use.

Status: milestone 1 (canonical normalizer). See SPEC.md for the full contract.

## Milestone 1: canonical normalizer

`server/library.py` exposes `normalize(src, dst)`: it turns any supported WAV
into the hub's one working form — 48 kHz, stereo, 32-bit IEEE float — the same
shape `wav_builder` already produces for the Spotykach, written with the plain
IEEE-float tag `0x0003` (not `WAVE_FORMAT_EXTENSIBLE`) so the Spotykach
firmware reads it. Every destination transform in later milestones renders
from this copy.

Two properties are pinned by tests:

- stateless / parallel-safe — ffmpeg is NOT a singleton; each `normalize` call
  runs its own subprocess with no shared lock or temp file, so parallel jobs
  (one per instrument) never contend.
- refuses what it cannot decode — a bad source raises `NormalizeError` instead
  of writing a corrupt file.

## Tests

    uv venv --python 3.11      # once
    uv pip install pytest
    .venv/bin/python -m pytest tests/ -q

Tests generate their source WAVs with ffmpeg and decode the normalized bytes
back, so every assertion runs against real bytes, never a mock.

Real-device tests are gated off by default. Tests that talk to a physical USB
instrument are marked `@pytest.mark.real_devices` and only run when you opt in:

    .venv/bin/python -m pytest tests/ --real-devices

## Layout

    server/library.py      canonical normalizer (milestone 1)
    tests/test_normalize.py
    SPEC.md                the contract this is built against
