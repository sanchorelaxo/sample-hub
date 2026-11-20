# Sample Hub

> One localhost web interface that moves a sample between every USB instrument
> on the desk, doing the format conversion for each destination so a drop
> always lands in a file the target can actually use.

Status: milestone 9d — stdlib HTTP server + vendored three.js rack client.
See SPEC.md for the full contract.

![rack client](docs/screenshot.png)

The rack with no instruments attached — every presence dot reads red, and the
library holds one cached sample (`808-kick.wav`) ready to drag onto a pane.

## Run

    .venv/bin/python -m server --port 8766

Then open http://127.0.0.1:8766/. Loopback only, stdlib only, no build step —
the client is plain ES modules with three.js vendored under `client/vendor/`.

The launcher wires the live hardware: every status poll auto-mounts a visible
but unmounted OP-1 / SD-card / RPI-RP2 volume via `udisksctl` (no sudo), and
each convert resolves its transfer roots from the same probe, so a drop lands
on the device that is actually present now. `SAMPLE_HUB_DATA` moves the
library/staging root (default `~/.sample-hub`).

## Milestone 9: server + rack client

`server/hub_server.py` is the HTTP layer: `GET /api/health`, `GET /api/status`
(presence dots), `GET /api/library`, `POST /api/convert` (one drop = one job),
`GET /api/jobs/<id>`, plus static serving of `client/` with
`Cache-Control: no-store`. Jobs are queued one worker thread per instrument.

`client/` is the front end: a flat shaded rack (`src/scene.js`, geometry in
`src/layout.js`, one number per instrument position in mm), DOM overlay panes
(`src/panes.js`) for the library and each instrument, presence-dot polling on a
20 s cadence (`src/status.js`), and HTML5 drag-and-drop into `/api/convert`
(`src/dragdrop.js`). The 3D layer is presentation only; all device I/O stays
behind the API.

## Milestone 1: canonical normalizer

`server/library.py` exposes `normalize(src, dst)`: it turns any supported WAV
into the hub's one working form — 48 kHz, stereo, 32-bit IEEE float — the same
shape `wav_builder` already produces for the Spotykach, written with the plain
IEEE-float tag `0x0003` (not `WAVE_FORMAT_EXTENSIBLE`) so the Spotykach
firmware reads it. Every destination transform renders from this copy.

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
back, so every assertion runs against real bytes, never a mock. Client-shell
tests serve the real `client/` directory and assert the assets are served with
`no-store`, that every module import is relative and resolves (no CDN), and
that each source module parses as an ES module.

Real-device tests are gated off by default. Tests that talk to a physical USB
instrument are marked `@pytest.mark.real_devices` and only run when you opt in:

    .venv/bin/python -m pytest tests/ --real-devices

## Layout

    server/hub_server.py   HTTP layer (milestone 9c)
    server/__main__.py     launcher: python -m server (milestone 9d)
    server/library.py      host cache + canonical normalizer (milestones 1, 9a)
    server/convert.py      conversion matrix, one fn per target
    server/devices/        per-target writers (op1, spotykach, wave_bard, ...)
    server/jobs.py         convert dispatch + per-instrument job queue (9b)
    server/status.py       per-instrument presence probe (milestone 8)
    client/                three.js rack client, no build step (milestone 9d)
    SPEC.md                the contract this is built against
